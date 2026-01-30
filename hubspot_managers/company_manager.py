"""
Company Manager for HubSpot operations
"""
import os
import logging
from typing import Dict, List, Optional, Any
from hubspot import HubSpot
from hubspot.crm.companies import SimplePublicObjectInputForCreate, ApiException
from hubspot.crm.companies.models import Filter, FilterGroup, PublicObjectSearchRequest

logger = logging.getLogger(__name__)


class CompanyManager:
    """Manages HubSpot company operations"""
    
    def __init__(self):
        access_token = os.getenv('HUBSPOT_ACCESS_TOKEN')
        if not access_token:
            raise ValueError("HUBSPOT_ACCESS_TOKEN not set")
        
        self.client = HubSpot(access_token=access_token)
        logger.info("CompanyManager initialized")
    
    def find_by_domain(self, domain: str) -> Optional[Dict]:
        """
        Find a company by domain
        
        Args:
            domain: Company domain (e.g., 'example.com')
        
        Returns:
            Company dict or None
        """
        if not domain:
            return None
        
        # Clean domain
        domain = domain.lower().replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
        
        try:
            # Search by domain
            filter = Filter(property_name="domain", operator="EQ", value=domain)
            filter_group = FilterGroup(filters=[filter])
            request = PublicObjectSearchRequest(
                filter_groups=[filter_group],
                properties=['name', 'domain', 'industry', 'numberofemployees', 'demo_request', 'priority']
            )
            
            response = self.client.crm.companies.search_api.do_search(request)
            
            if response.results:
                company = response.results[0]
                return {
                    'id': company.id,
                    'properties': company.properties,
                    'created_at': company.created_at,
                    'updated_at': company.updated_at
                }
            
            return None
            
        except ApiException as e:
            logger.error(f"Error searching company by domain {domain}: {e}")
            return None
    
    def create(self, name: str, domain: str = "", properties: Dict = None) -> Optional[Dict]:
        """
        Create a new company
        
        Args:
            name: Company name
            domain: Company domain (optional; omit if empty to avoid HubSpot validation issues)
            properties: Additional properties
        
        Returns:
            Created company dict or None
        """
        try:
            props = {'name': name.strip() or "Unknown", **(properties or {})}
            # Only include domain when non-empty; HubSpot accepts name-only, empty string can cause issues
            domain_clean = (domain or "").strip()
            if domain_clean:
                domain_clean = domain_clean.lower().replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
                props['domain'] = domain_clean
            
            input_obj = SimplePublicObjectInputForCreate(properties=props)
            response = self.client.crm.companies.basic_api.create(input_obj)
            
            logger.info(f"Created company: {name} ({props.get('domain', 'no domain')}) - ID: {response.id}")
            
            return {
                'id': response.id,
                'properties': response.properties,
                'created_at': response.created_at,
                'updated_at': response.updated_at
            }
            
        except ApiException as e:
            err_msg = getattr(e, 'body', None) or str(e)
            logger.error(f"Error creating company {name}: {err_msg}")
            raise ValueError(f"HubSpot company create failed: {err_msg}") from e
    
    def update(self, company_id: str, properties: Dict) -> Optional[Dict]:
        """
        Update a company
        
        Args:
            company_id: HubSpot company ID
            properties: Properties to update
        
        Returns:
            Updated company dict or None
        """
        try:
            from hubspot.crm.companies import SimplePublicObjectInput
            
            input_obj = SimplePublicObjectInput(properties=properties)
            response = self.client.crm.companies.basic_api.update(
                company_id=company_id,
                simple_public_object_input=input_obj
            )
            
            logger.info(f"Updated company {company_id}")
            
            return {
                'id': response.id,
                'properties': response.properties,
                'created_at': response.created_at,
                'updated_at': response.updated_at
            }
            
        except ApiException as e:
            logger.error(f"Error updating company {company_id}: {e}")
            return None
    
    def find_or_create(self, name: str, domain: str = "", industry: str = None, properties: Dict = None) -> Optional[Dict]:
        """
        Find existing company or create new one
        
        Args:
            name: Company name
            domain: Company domain (optional)
            industry: Company industry
            properties: Additional properties
        
        Returns:
            Company dict (existing or new)
        """
        domain_clean = (domain or "").strip()
        if domain_clean:
            domain_clean = domain_clean.lower().replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            existing = self.find_by_domain(domain_clean)
            if existing:
                logger.info(f"Found existing company for {domain_clean}: {existing['id']}")
                return existing
        
        # Create new
        props = dict(properties or {})
        if industry:
            props['industry'] = industry
        
        return self.create(name, domain_clean or domain, props)
