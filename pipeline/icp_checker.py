#!/usr/bin/env python3
"""
ICP (Ideal Customer Profile) checker for lead prioritization.
Evaluates leads against defined criteria and returns priority scores.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from pipeline.enrichment import EnrichmentResult

logger = logging.getLogger(__name__)


class Priority(Enum):
    """Lead priority levels"""
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DISQUALIFIED = "disqualified"


@dataclass
class ICPResult:
    """Result of ICP evaluation"""
    priority: Priority
    score: int
    reasons: List[str]
    missing_data: List[str]
    recommendations: List[str]
    is_agency: bool = False
    
    @property
    def is_qualified(self) -> bool:
        return self.priority != Priority.DISQUALIFIED
    
    @property
    def needs_review(self) -> bool:
        return len(self.missing_data) > 0 and self.priority not in [Priority.VERY_HIGH, Priority.DISQUALIFIED]


class ICPChecker:
    """
    Evaluates leads against ICP criteria using the Zenyt ICP Framework.
    
    Scoring model (0-100 points):
    - Company match (50 pts):
      - Website ↔ LinkedIn domain exact match: 15
      - Employee count ≥ 50: 15 (≥ 200 add +5 bonus = 20)
      - Independent brand: 10
      - Industry in target set: 10
    - Catalog/velocity (20 pts):
      - SKU proxy ≥ 50: 10
      - Frequent promos/new arrivals: 5
      - Multi-variant PDPs/configurators: 5
    - Persona density (20 pts):
      - ≥ 3 relevant roles: 10
      - Senior role present: 5
      - Titles aligned with ICP: 5
    - Timing/context (10 pts):
      - Signals of replatforming: 5
      - Multi-channel (Amazon/wholesale/DTC): 5
    
    Priority thresholds:
    - 80-100: Strong ICP (VERY_HIGH) — prioritize for outreach
    - 60-79: ICP-adjacent (HIGH) — validate missing fields then import
    - 40-59: Weak match (MEDIUM) — nurture sequence
    - 20-39: Poor fit (LOW) — long-term nurture
    - <20: DISQUALIFIED — park or reject
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.icp = self.settings.icp
        
        # Target industries from ICP framework
        self.target_industries = [
            'fashion', 'beauty', 'electronics', 'home goods', 
            'consumer lifestyle', 'jewelry', 'home', 'apparel'
        ]
        
        # Agency/holding company patterns (to detect non-brands)
        self.non_brand_patterns = [
            'holding', 'holdings', 'ventures', 'capital', 'partners',
            'agency', 'consulting', 'solutions provider', 'group of brands'
        ]
        
        # Multi-channel signals
        self.multichannel_signals = [
            'amazon', 'wholesale', 'retail partners', 'marketplaces',
            'dtc', 'direct-to-consumer', 'omnichannel', 'multi-channel'
        ]
        
        # Replatforming/tech signals
        self.tech_signals = [
            'replatform', 'migration', 'redesign', 'shopify plus',
            'headless', 'composable', 'pwa', 'jamstack'
        ]
    
    def is_independent_brand(self, company_name: str = None, description: str = None) -> bool:
        """Check if company is an independent brand (not holding/agency)"""
        text = f"{company_name or ''} {description or ''}".lower()
        
        for pattern in self.non_brand_patterns:
            if pattern in text:
                return False
        
        return True
    
    def check_linkedin_match(self, lead_domain: str, linkedin_domain: str = None) -> tuple[int, Optional[str]]:
        """
        Company match: Website ↔ LinkedIn domain exact match (15 pts)
        """
        if not linkedin_domain:
            return 0, "LinkedIn domain not available"
        
        # Normalize domains for comparison
        lead_apex = lead_domain.lower().replace('www.', '').strip('/')
        linkedin_apex = linkedin_domain.lower().replace('www.', '').strip('/')
        
        if lead_apex == linkedin_apex:
            return 15, None
        else:
            return 0, f"LinkedIn domain mismatch ({linkedin_apex})"
    
    def evaluate_employees(self, employee_count: Optional[int]) -> tuple[int, Optional[str]]:
        """
        Employee count scoring (15-20 pts):
        - ≥ 200: 20 pts
        - ≥ 50: 15 pts
        - < 50: 0 pts (ICP requirement not met)
        """
        if employee_count is None:
            return 0, "Employee count unknown"
        
        if employee_count >= 200:
            return 20, None  # Bonus for larger orgs
        elif employee_count >= 50:
            return 15, None  # ICP minimum met
        else:
            return 0, f"Below ICP minimum ({employee_count} < 50 employees)"
    
    def evaluate_brand_independence(self, company_name: str = None, description: str = None) -> tuple[int, Optional[str]]:
        """
        Independent brand check (10 pts)
        Must be standalone brand, not holding company
        """
        if self.is_independent_brand(company_name, description):
            return 10, None
        else:
            return 0, "Appears to be holding company/agency"
    
    def evaluate_industry(self, industry: Optional[str]) -> tuple[int, Optional[str]]:
        """
        Industry fit (10 pts)
        Must be in: fashion, beauty, electronics, home goods, consumer lifestyle
        """
        if not industry:
            return 0, "Industry unknown"
        
        industry_lower = industry.lower()
        
        # Check exact matches and partial matches
        for target in self.target_industries:
            if target in industry_lower or industry_lower in target:
                return 10, None
        
        return 0, f"Non-target industry ({industry})"
    
    def evaluate_catalog(self, sku_count: Optional[int], tech_stack: List[str] = None) -> tuple[int, Optional[str]]:
        """
        Catalog/velocity scoring (20 pts total):
        - SKU proxy ≥ 50: 10 pts
        - Frequent promos/new arrivals: 5 pts (proxy: tech stack has marketing tools)
        - Multi-variant PDPs: 5 pts (proxy: e-commerce platform detected)
        """
        score = 0
        reasons = []
        
        # SKU count
        if sku_count and sku_count >= 50:
            score += 10
        elif sku_count:
            reasons.append(f"Small catalog ({sku_count} < 50 SKUs)")
        else:
            reasons.append("SKU count unknown")
        
        # Proxy signals from tech stack
        if tech_stack:
            # Marketing/promo tools
            marketing_tools = ['klaviyo', 'mailchimp', 'attentive', 'yotpo']
            if any(tool.lower() in ' '.join(tech_stack).lower() for tool in marketing_tools):
                score += 5
            
            # Multi-variant/configurator signals
            ecom_platforms = ['shopify', 'magento', 'bigcommerce', 'vtex']
            if any(platform.lower() in ' '.join(tech_stack).lower() for platform in ecom_platforms):
                score += 5
        
        reason = '; '.join(reasons) if reasons else None
        return score, reason
    
    def evaluate_persona_density(self, job_title: str = None) -> tuple[int, Optional[str]]:
        """
        Persona density (20 pts):
        - ≥ 3 relevant roles: 10 pts (can't measure from single lead, give partial credit)
        - Senior role present: 5 pts
        - Titles aligned with ICP: 5 pts
        
        ICP roles: Ecommerce, E-Commerce, DTC, Digital, Merchandising, Product, Growth, Technology
        Senior titles: Head, VP, Director, Chief
        """
        if not job_title:
            return 0, "Job title unknown"
        
        score = 0
        title_lower = job_title.lower()
        
        # Check for ICP-aligned titles
        icp_keywords = ['ecommerce', 'e-commerce', 'dtc', 'digital', 'merchandising', 
                        'product', 'growth', 'technology', 'online', 'web']
        if any(keyword in title_lower for keyword in icp_keywords):
            score += 5
        
        # Check for senior role
        senior_keywords = ['head', 'vp', 'director', 'chief', 'vice president', 'ceo', 'cto', 'cmo']
        if any(keyword in title_lower for keyword in senior_keywords):
            score += 5
        
        # Assume single lead = partial persona density (can't see full org)
        if score > 0:
            score += 5  # Partial credit for having at least one relevant persona
        
        return score, None if score > 0 else "Role not aligned with ICP"
    
    def evaluate_timing_context(self, description: str = None, tech_stack: List[str] = None) -> tuple[int, Optional[str]]:
        """
        Timing/context signals (10 pts):
        - Replatforming signals: 5 pts
        - Multi-channel: 5 pts
        """
        score = 0
        text = (description or '').lower()
        
        if tech_stack:
            text += ' ' + ' '.join(tech_stack).lower()
        
        # Replatforming signals
        if any(signal in text for signal in self.tech_signals):
            score += 5
        
        # Multi-channel signals
        if any(signal in text for signal in self.multichannel_signals):
            score += 5
        
        return score, None
    
    def calculate_priority(self, score: int) -> Priority:
        """
        Convert score to priority level per ICP framework:
        - 80-100: Strong ICP (VERY_HIGH)
        - 60-79: ICP-adjacent (HIGH)
        - 40-59: Weak match (MEDIUM)
        - 20-39: Poor fit (LOW)
        - <20: DISQUALIFIED
        """
        if score >= 80:
            return Priority.VERY_HIGH
        elif score >= 60:
            return Priority.HIGH
        elif score >= 40:
            return Priority.MEDIUM
        elif score >= 20:
            return Priority.LOW
        else:
            return Priority.DISQUALIFIED
    
    def evaluate(
        self,
        enrichment: EnrichmentResult,
        employee_count: Optional[int] = None,
        revenue: Optional[str] = None,  # Now accepts revenue range string
        sku_count: Optional[int] = None,
        linkedin_url: Optional[str] = None,
        job_title: Optional[str] = None,
        has_meeting: bool = False,
        additional_data: Dict[str, Any] = None
    ) -> ICPResult:
        """
        Evaluate a lead against ICP criteria using Zenyt ICP Framework.
        
        Scoring breakdown (0-100 pts):
        - Company match (50 pts): LinkedIn match (15) + Employees (15-20) + Brand independence (10) + Industry (10)
        - Catalog/velocity (20 pts): SKU count (10) + Promo signals (5) + Multi-variant PDPs (5)
        - Persona density (20 pts): Relevant roles (10) + Senior role (5) + ICP alignment (5)
        - Timing/context (10 pts): Replatforming (5) + Multi-channel (5)
        
        Args:
            enrichment: Web/Apollo enrichment result
            employee_count: Number of employees from Apollo/LinkedIn
            revenue: Revenue range string (e.g., "$10M - $50M")
            sku_count: Product count (if known)
            linkedin_url: Company LinkedIn URL for domain matching
            job_title: Contact's job title for persona scoring
            has_meeting: Whether a meeting is already booked
            additional_data: Any additional data for evaluation
        
        Returns:
            ICPResult with priority (based on thresholds), score, and recommendations
        """
        total_score = 0
        reasons = []
        missing_data = []
        recommendations = []
        
        # Extract LinkedIn domain from URL if provided
        linkedin_domain = None
        if linkedin_url:
            import re
            match = re.search(r'company/([^/?]+)', linkedin_url)
            if match:
                linkedin_domain = match.group(1)
        
        # ========== COMPANY MATCH (50 pts) ==========
        
        # 1. LinkedIn domain match (15 pts)
        linkedin_score, linkedin_reason = self.check_linkedin_match(
            enrichment.domain, 
            linkedin_domain
        )
        total_score += linkedin_score
        if linkedin_reason:
            if "not available" in linkedin_reason:
                missing_data.append("linkedin_url")
            else:
                reasons.append(linkedin_reason)
        
        # 2. Employee count (15-20 pts)
        emp_score, emp_reason = self.evaluate_employees(employee_count)
        total_score += emp_score
        if emp_reason:
            if "unknown" in emp_reason:
                missing_data.append("employee_count")
            else:
                reasons.append(emp_reason)
        
        # 3. Independent brand (10 pts)
        brand_score, brand_reason = self.evaluate_brand_independence(
            enrichment.company_name,
            enrichment.description
        )
        total_score += brand_score
        if brand_reason:
            reasons.append(brand_reason)
        
        # 4. Industry (10 pts)
        ind_score, ind_reason = self.evaluate_industry(enrichment.industry)
        total_score += ind_score
        if ind_reason:
            if "unknown" in ind_reason:
                missing_data.append("industry")
            else:
                reasons.append(ind_reason)
        
        # ========== CATALOG/VELOCITY (20 pts) ==========
        
        catalog_score, catalog_reason = self.evaluate_catalog(
            sku_count,
            enrichment.tech_stack
        )
        total_score += catalog_score
        if catalog_reason:
            reasons.append(catalog_reason)
        
        # ========== PERSONA DENSITY (20 pts) ==========
        
        persona_score, persona_reason = self.evaluate_persona_density(job_title)
        total_score += persona_score
        if persona_reason:
            if "unknown" in persona_reason:
                missing_data.append("job_title")
            else:
                reasons.append(persona_reason)
        
        # ========== TIMING/CONTEXT (10 pts) ==========
        
        context_score, _ = self.evaluate_timing_context(
            enrichment.description,
            enrichment.tech_stack
        )
        total_score += context_score
        
        # ========== ADDITIONAL CONTEXT ==========
        
        # Check revenue range (informational, not scored directly)
        if revenue:
            # Parse revenue to check if in ICP range ($10M-$500M)
            if any(marker in revenue for marker in ['$10M', '$50M', '$100M', '$500M']):
                reasons.append(f"Revenue in ICP range ({revenue})")
            elif '$1B' in revenue or '$500M' in revenue:
                reasons.append(f"Enterprise segment ({revenue})")
        else:
            missing_data.append("revenue")
        
        # Meeting boost (informational, meeting already counts in other systems)
        if has_meeting:
            reasons.append("🎯 Meeting scheduled — high priority")
        
        # ========== QUALIFICATION ==========
        
        # Calculate priority based on ICP framework thresholds
        priority = self.calculate_priority(total_score)
        
        # Check if brand is actually independent
        is_agency = not self.is_independent_brand(
            enrichment.company_name,
            enrichment.description
        )
        
        # Generate recommendations per ICP framework
        if priority == Priority.VERY_HIGH:
            recommendations.append("✅ Strong ICP — Prioritize for outreach")
            recommendations.append("Immediate personalized follow-up")
            recommendations.append("Assign to senior sales rep")
        elif priority == Priority.HIGH:
            recommendations.append("✓ ICP-adjacent — Validate missing fields")
            recommendations.append("Standard outreach sequence")
            if missing_data:
                recommendations.append(f"Enrich: {', '.join(missing_data)}")
        elif priority == Priority.MEDIUM:
            recommendations.append("⚠ Weak match — Nurture sequence")
            recommendations.append("Enrich data before outreach")
        elif priority == Priority.LOW:
            recommendations.append("⊘ Poor fit — Long-term nurture or park")
        else:  # DISQUALIFIED
            recommendations.append("✗ Disqualified — Does not meet ICP criteria")
            recommendations.append("Consider rejecting or park for later")
        
        if is_agency:
            recommendations.append("⚠ Non-brand entity detected (holding/agency)")
        
        result = ICPResult(
            priority=priority,
            score=total_score,
            reasons=reasons,
            missing_data=missing_data,
            recommendations=recommendations,
            is_agency=is_agency
        )
        
        logger.info(f"ICP evaluation for {enrichment.domain}: {priority.value} (score: {total_score}/100)")
        
        return result

