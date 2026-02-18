#!/usr/bin/env python3
"""
Web enrichment module.
Fetches company information from websites for lead enrichment.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """Result of web enrichment"""
    domain: str
    company_name: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    employee_count: Optional[int] = None
    estimated_revenue: Optional[int] = None
    revenue_display: Optional[str] = None  # Human readable revenue like "$10M-$50M"
    technologies: List[str] = field(default_factory=list)
    social_links: Dict[str, str] = field(default_factory=dict)
    ecommerce_platform: Optional[str] = None
    has_ecommerce: bool = False
    is_agency: bool = False  # Explicitly track if it's an agency
    success: bool = False
    error: Optional[str] = None


class WebEnricher:
    """
    Lightweight web enrichment for lead data.
    
    Fetches basic company information from websites without
    heavy dependencies on external APIs.
    """
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; ZenytBot/1.0)'
        })
        
        # E-commerce platform detection patterns
        self.ecommerce_patterns = {
            'shopify': [
                r'cdn\.shopify\.com',
                r'shopify\.com',
                r'myshopify\.com'
            ],
            'woocommerce': [
                r'woocommerce',
                r'wc-ajax'
            ],
            'magento': [
                r'mage',
                r'magento',
                r'varien'
            ],
            'bigcommerce': [
                r'bigcommerce',
                r'cdn11\.bigcommerce\.com'
            ],
            'salesforce_commerce': [
                r'demandware',
                r'salesforce\.com'
            ]
        }
        
        # Industry keyword detection
        self.industry_keywords = {
            'fashion': ['fashion', 'clothing', 'apparel', 'wear', 'boutique', 'style'],
            'beauty': ['beauty', 'cosmetics', 'skincare', 'makeup', 'hair'],
            'electronics': ['electronics', 'tech', 'gadget', 'computer', 'phone'],
            'home': ['home', 'furniture', 'decor', 'interior', 'kitchen'],
            'food': ['food', 'grocery', 'gourmet', 'organic', 'beverage'],
            'sports': ['sports', 'fitness', 'outdoor', 'athletic', 'gym'],
            'jewelry': ['jewelry', 'jewellery', 'watch', 'diamond', 'ring'],
            'automotive': ['auto', 'car', 'vehicle', 'motor', 'parts'],
            'health': ['health', 'wellness', 'supplement', 'vitamin', 'pharma'],
            'pet': ['pet', 'dog', 'cat', 'animal', 'vet']
        }
        
        # Agency detection keywords
        self.agency_keywords = [
            'agency', 'digital agency', 'marketing agency', 'creative agency',
            'consulting', 'consultancy', 'solutions', 'services', 'partner',
            'advertising', 'media agency', 'web agency', 'design agency',
            'we help brands', 'our clients', 'case studies', 'our work',
            'ecommerce agency', 'shopify partner', 'magento partner',
            'performance marketing', 'growth agency', 'seo agency'
        ]
    
    def normalize_domain(self, url_or_domain: str) -> str:
        """Normalize URL to apex domain"""
        if not url_or_domain:
            return ''
        
        url = url_or_domain.strip().lower()
        
        # Add protocol if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split('/')[0]
        
        # Remove www prefix
        if domain.startswith('www.'):
            domain = domain[4:]
        
        return domain
    
    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a webpage and return HTML content"""
        try:
            response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None
    
    def detect_ecommerce_platform(self, html: str) -> Optional[str]:
        """Detect e-commerce platform from HTML content"""
        html_lower = html.lower()
        
        for platform, patterns in self.ecommerce_patterns.items():
            for pattern in patterns:
                if re.search(pattern, html_lower):
                    return platform
        
        return None
    
    def _has_cart_features(self, html: str) -> bool:
        """Check if site has e-commerce/cart features"""
        html_lower = html.lower()
        cart_indicators = [
            'add to cart', 'add-to-cart', 'addtocart',
            'shopping cart', 'checkout', 'buy now',
            'product-price', '/product/', '/products/',
            '/shop/', '/store/', 'cart-icon'
        ]
        matches = sum(1 for ind in cart_indicators if ind in html_lower)
        return matches >= 2  # At least 2 indicators
    
    def detect_industry(self, text: str) -> Optional[str]:
        """Detect industry from text content"""
        text_lower = text.lower()
        
        scores = {}
        for industry, keywords in self.industry_keywords.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[industry] = score
        
        if scores:
            return max(scores, key=scores.get)
        
        return None
    
    def detect_agency(self, text: str, domain: str) -> bool:
        """Detect if the website is an agency"""
        text_lower = text.lower()
        domain_lower = domain.lower()
        
        # Check domain for agency patterns
        agency_domain_patterns = ['agency', 'digital', 'creative', 'studio', 'partner', 'consulting']
        for pattern in agency_domain_patterns:
            if pattern in domain_lower:
                return True
        
        # Check content for agency keywords
        agency_score = sum(1 for kw in self.agency_keywords if kw in text_lower)
        
        # If multiple agency keywords found, likely an agency
        return agency_score >= 2
    
    def extract_company_name(self, soup: BeautifulSoup, domain: str) -> Optional[str]:
        """Extract company name from page"""
        # Try og:site_name
        og_site = soup.find('meta', property='og:site_name')
        if og_site and og_site.get('content'):
            return og_site['content'].strip()
        
        # Try title
        title = soup.find('title')
        if title:
            title_text = title.get_text().strip()
            # Clean up common suffixes
            for sep in [' | ', ' - ', ' :: ', ' — ']:
                if sep in title_text:
                    return title_text.split(sep)[0].strip()
            return title_text[:50]  # Limit length
        
        # Fall back to domain
        return domain.split('.')[0].title()
    
    def extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract company description from meta tags"""
        # Try og:description
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            return og_desc['content'].strip()[:500]
        
        # Try meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            return meta_desc['content'].strip()[:500]
        
        return None
    
    def extract_social_links(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract social media links"""
        social_patterns = {
            'linkedin': r'linkedin\.com',
            'twitter': r'twitter\.com|x\.com',
            'facebook': r'facebook\.com',
            'instagram': r'instagram\.com',
            'youtube': r'youtube\.com'
        }
        
        social_links = {}
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            for platform, pattern in social_patterns.items():
                if re.search(pattern, href) and platform not in social_links:
                    social_links[platform] = href
        
        return social_links
    
    def enrich(self, domain: str) -> EnrichmentResult:
        """
        Enrich lead data by fetching and analyzing the company website.
        
        Args:
            domain: Company domain or URL
        
        Returns:
            EnrichmentResult with extracted data
        """
        result = EnrichmentResult(domain=domain)
        
        # Normalize domain
        clean_domain = self.normalize_domain(domain)
        if not clean_domain:
            result.error = "Invalid domain"
            return result
        
        result.domain = clean_domain
        
        # Fetch homepage
        url = f"https://{clean_domain}"
        html = self.fetch_page(url)
        
        if not html:
            # Try with www
            url = f"https://www.{clean_domain}"
            html = self.fetch_page(url)
        
        if not html:
            result.error = "Failed to fetch website"
            return result
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Extract data
            result.company_name = self.extract_company_name(soup, clean_domain)
            result.description = self.extract_description(soup)
            result.social_links = self.extract_social_links(soup)
            
            # Get page text for analysis
            page_text = soup.get_text()
            
            # Detect if agency
            result.is_agency = self.detect_agency(page_text, clean_domain)
            
            # Detect e-commerce (only if not an agency)
            if not result.is_agency:
                result.ecommerce_platform = self.detect_ecommerce_platform(html)
                result.has_ecommerce = result.ecommerce_platform is not None or self._has_cart_features(html)
            
            # Detect industry from content
            result.industry = self.detect_industry(page_text)
            
            result.success = True
            type_str = "Agency" if result.is_agency else ("E-commerce" if result.has_ecommerce else "Brand")
            logger.info(f"Enriched {clean_domain}: {result.company_name}, type={type_str}, industry={result.industry}")
            
        except Exception as e:
            logger.error(f"Error enriching {domain}: {e}")
            result.error = str(e)
        
        return result
    

