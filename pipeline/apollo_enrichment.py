#!/usr/bin/env python3
"""
Apollo.io API Integration for Company & Person Enrichment

Provides:
- Company revenue (accurate!)
- Employee count
- Industry
- Technologies used
- Founded year
- Headquarters location
- LinkedIn URL (company and person)
"""

import os
import requests
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ApolloCompanyData:
    """Company data from Apollo.io"""
    company_name: str
    domain: str
    revenue: Optional[str] = None  # e.g., "$10M - $50M"
    revenue_range: Optional[str] = None
    employee_count: Optional[int] = None
    employee_range: Optional[str] = None  # e.g., "50-200"
    industry: Optional[str] = None
    technologies: List[str] = None
    founded_year: Optional[int] = None
    headquarters: Optional[str] = None
    linkedin_url: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    annual_revenue: Optional[float] = None  # Numeric value

    def to_dict(self) -> Dict:
        """Serialize to dict for apollo_contact_company / apollo_scanned_url storage."""
        return {
            "company_name": self.company_name,
            "domain": self.domain,
            "revenue": self.revenue,
            "revenue_range": self.revenue_range,
            "employee_count": self.employee_count,
            "employee_range": self.employee_range,
            "industry": self.industry,
            "technologies": self.technologies or [],
            "founded_year": self.founded_year,
            "headquarters": self.headquarters,
            "linkedin_url": self.linkedin_url,
            "phone": self.phone,
            "description": self.description,
        }


@dataclass
class ApolloPersonData:
    """Person data from Apollo.io People Match"""
    linkedin_url: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    headline: Optional[str] = None
    organization_name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "linkedin_url": self.linkedin_url,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "title": self.title,
            "headline": self.headline,
            "organization_name": self.organization_name,
            "city": self.city,
            "country": self.country,
        }


class ApolloEnricher:
    """Enrich companies and people using Apollo.io API"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("APOLLO_API_KEY")
        self.base_url = "https://api.apollo.io/api/v1"
        
        if not self.api_key:
            logger.warning("⚠️  APOLLO_API_KEY not found - Apollo enrichment disabled")
    
    def enrich_person(
        self,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        organization_name: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Optional[ApolloPersonData]:
        """
        Find a person using Apollo People Match API.
        Tries email first, then falls back to name + company.
        
        Returns:
            ApolloPersonData with linkedin_url or None
        """
        if not self.api_key:
            return None

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.api_key,
        }
        url = f"{self.base_url}/people/match"

        # Strategy 1: match by email
        if email:
            try:
                payload = {"email": email, "reveal_personal_emails": False}
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                if resp.status_code == 200:
                    person = resp.json().get("person")
                    if person and person.get("linkedin_url"):
                        logger.info(f"Apollo People Match found person by email: {email}")
                        return self._parse_person(person)
            except Exception as e:
                logger.warning(f"Apollo People Match (email) error: {e}")

        # Strategy 2: match by name + organization
        if first_name and (organization_name or domain):
            try:
                payload = {
                    "first_name": first_name,
                    "last_name": last_name or "",
                    "organization_name": organization_name or "",
                    "domain": domain or "",
                    "reveal_personal_emails": False,
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                if resp.status_code == 200:
                    person = resp.json().get("person")
                    if person and person.get("linkedin_url"):
                        logger.info(f"Apollo People Match found person by name: {first_name} {last_name}")
                        return self._parse_person(person)
            except Exception as e:
                logger.warning(f"Apollo People Match (name) error: {e}")

        logger.info(f"Apollo People Match: no LinkedIn found for {email or first_name}")
        return None

    def _parse_person(self, person: Dict) -> ApolloPersonData:
        org = person.get("organization") or {}
        return ApolloPersonData(
            linkedin_url=person.get("linkedin_url"),
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            title=person.get("title"),
            headline=person.get("headline"),
            organization_name=org.get("name") or person.get("organization_name"),
            city=person.get("city"),
            country=person.get("country"),
        )

    def enrich_company(self, domain: str) -> Optional[ApolloCompanyData]:
        """
        Enrich a company by domain using Apollo.io
        
        Args:
            domain: Company domain (e.g., "zenyt.ai")
            
        Returns:
            ApolloCompanyData or None if not found/error
        """
        if not self.api_key:
            return None
        
        try:
            # Apollo enrichment endpoint
            url = f"{self.base_url}/organizations/enrich"
            
            headers = {
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "X-Api-Key": self.api_key
            }
            
            params = {
                "domain": domain
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                organization = data.get("organization", {})
                
                if organization:
                    return self._parse_apollo_response(organization)
                else:
                    logger.warning(f"No organization data found for {domain}")
                    return None
                    
            elif response.status_code == 404:
                logger.info(f"Company not found in Apollo: {domain}")
                return None
            else:
                logger.warning(f"Apollo API error for {domain}: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Apollo API timeout for {domain}")
            return None
        except Exception as e:
            logger.error(f"Error enriching {domain} with Apollo: {e}")
            return None
    
    def _parse_apollo_response(self, org: Dict) -> ApolloCompanyData:
        """Parse Apollo.io organization response"""
        
        # Extract revenue information
        revenue_range = None
        annual_revenue = None
        
        if org.get("estimated_num_employees"):
            # Apollo provides employee count
            employee_count = org.get("estimated_num_employees")
            employee_range = self._format_employee_range(employee_count)
        else:
            employee_count = None
            employee_range = None
        
        # Revenue from Apollo
        if org.get("annual_revenue"):
            annual_revenue = org.get("annual_revenue")
            revenue_range = self._format_revenue_range(annual_revenue)
        
        # Technologies
        technologies = []
        if org.get("technologies"):
            technologies = [tech.get("name") for tech in org.get("technologies", []) if tech.get("name")][:10]
        
        # LinkedIn URL
        linkedin_url = org.get("linkedin_url")
        
        # Headquarters
        headquarters = None
        if org.get("city") and org.get("country"):
            headquarters = f"{org.get('city')}, {org.get('country')}"
        elif org.get("city"):
            headquarters = org.get("city")
        elif org.get("country"):
            headquarters = org.get("country")
        
        return ApolloCompanyData(
            company_name=org.get("name", ""),
            domain=org.get("domain", ""),
            revenue=revenue_range,
            revenue_range=revenue_range,
            employee_count=employee_count,
            employee_range=employee_range,
            industry=org.get("industry"),
            technologies=technologies,
            founded_year=org.get("founded_year"),
            headquarters=headquarters,
            linkedin_url=linkedin_url,
            phone=org.get("phone"),
            description=org.get("short_description") or org.get("description"),
            annual_revenue=annual_revenue
        )
    
    def _format_revenue_range(self, revenue: float) -> str:
        """Format revenue as human-readable range"""
        if not revenue:
            return None
        
        # Apollo returns revenue in dollars
        if revenue < 1_000_000:
            return f"${int(revenue/1000)}K"
        elif revenue < 10_000_000:
            return f"${int(revenue/1_000_000)}M - ${int(revenue/1_000_000) + 2}M"
        elif revenue < 50_000_000:
            return "$10M - $50M"
        elif revenue < 100_000_000:
            return "$50M - $100M"
        elif revenue < 500_000_000:
            return "$100M - $500M"
        elif revenue < 1_000_000_000:
            return "$500M - $1B"
        else:
            return "$1B+"
    
    def _format_employee_range(self, count: int) -> str:
        """Format employee count as range"""
        if not count:
            return None
        
        if count < 10:
            return "1-10"
        elif count < 50:
            return "10-50"
        elif count < 200:
            return "50-200"
        elif count < 500:
            return "200-500"
        elif count < 1000:
            return "500-1,000"
        elif count < 5000:
            return "1,000-5,000"
        elif count < 10000:
            return "5,000-10,000"
        else:
            return "10,000+"


def enrich_with_apollo(domain: str, api_key: Optional[str] = None) -> Optional[ApolloCompanyData]:
    """
    Convenience function to enrich a single domain with Apollo
    
    Usage:
        data = enrich_with_apollo("zenyt.ai")
        if data:
            print(f"Revenue: {data.revenue_range}")
            print(f"Employees: {data.employee_range}")
    """
    enricher = ApolloEnricher(api_key)
    return enricher.enrich_company(domain)


if __name__ == "__main__":
    # Test enrichment
    import sys
    from dotenv import load_dotenv
    from pathlib import Path
    
    load_dotenv(Path(__file__).parent.parent / ".env")
    
    test_domain = sys.argv[1] if len(sys.argv) > 1 else "shopify.com"
    
    print(f"\n🔍 Testing Apollo enrichment for: {test_domain}\n")
    
    result = enrich_with_apollo(test_domain)
    
    if result:
        print(f"✅ Company: {result.company_name}")
        print(f"💰 Revenue: {result.revenue_range or 'Unknown'}")
        print(f"👥 Employees: {result.employee_range or result.employee_count or 'Unknown'}")
        print(f"🏢 Industry: {result.industry or 'Unknown'}")
        print(f"📅 Founded: {result.founded_year or 'Unknown'}")
        print(f"📍 HQ: {result.headquarters or 'Unknown'}")
        print(f"🔗 LinkedIn: {result.linkedin_url or 'Unknown'}")
        if result.technologies:
            print(f"💻 Tech Stack: {', '.join(result.technologies[:5])}")
    else:
        print("❌ No data found")
