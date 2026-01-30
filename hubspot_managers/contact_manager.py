"""
Contact Manager for HubSpot operations
"""
import os
import logging
from typing import Dict, List, Optional, Any
from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate, ApiException
from hubspot.crm.contacts.models import Filter, FilterGroup, PublicObjectSearchRequest
from hubspot.crm.associations.v4 import BatchInputPublicDefaultAssociationMultiPost

logger = logging.getLogger(__name__)


class ContactManager:
    """Manages HubSpot contact operations"""
    
    def __init__(self):
        access_token = os.getenv('HUBSPOT_ACCESS_TOKEN')
        if not access_token:
            raise ValueError("HUBSPOT_ACCESS_TOKEN not set")
        
        self.client = HubSpot(access_token=access_token)
        logger.info("ContactManager initialized")
    
    def find_by_email(self, email: str) -> Optional[Dict]:
        """
        Find a contact by email
        
        Args:
            email: Contact email
        
        Returns:
            Contact dict or None
        """
        if not email:
            return None
        
        email = email.lower().strip()
        
        try:
            # Search by email
            filter = Filter(property_name="email", operator="EQ", value=email)
            filter_group = FilterGroup(filters=[filter])
            request = PublicObjectSearchRequest(
                filter_groups=[filter_group],
                properties=['email', 'firstname', 'lastname', 'phone', 'company', 'jobtitle']
            )
            
            response = self.client.crm.contacts.search_api.do_search(request)
            
            if response.results:
                contact = response.results[0]
                return {
                    'id': contact.id,
                    'properties': contact.properties,
                    'created_at': contact.created_at,
                    'updated_at': contact.updated_at
                }
            
            return None
            
        except ApiException as e:
            logger.error(f"Error searching contact by email {email}: {e}")
            return None
    
    def create(self, email: str, firstname: str = None, lastname: str = None, 
               properties: Dict = None, company_id: str = None) -> Optional[Dict]:
        """
        Create a new contact
        
        Args:
            email: Contact email
            firstname: First name
            lastname: Last name
            properties: Additional properties
            company_id: HubSpot company ID to associate with
        
        Returns:
            Created contact dict or None
        """
        try:
            props = {
                'email': email,
                **(properties or {})
            }
            
            if firstname:
                props['firstname'] = firstname
            if lastname:
                props['lastname'] = lastname
            
            input_obj = SimplePublicObjectInputForCreate(properties=props)
            response = self.client.crm.contacts.basic_api.create(input_obj)
            
            logger.info(f"Created contact: {email} - ID: {response.id}")
            
            # Associate with company if provided
            if company_id:
                self._associate_with_company(response.id, company_id)
            
            return {
                'id': response.id,
                'properties': response.properties,
                'created_at': response.created_at,
                'updated_at': response.updated_at
            }
            
        except ApiException as e:
            logger.error(f"Error creating contact {email}: {e}")
            return None
    
    def _associate_with_company(self, contact_id: str, company_id: str):
        """Associate a contact with a company"""
        try:
            from hubspot.crm.associations.v4 import AssociationSpec
            
            self.client.crm.associations.v4.basic_api.create(
                object_type='contacts',
                object_id=contact_id,
                to_object_type='companies',
                to_object_id=company_id,
                association_spec=[AssociationSpec(
                    association_category='HUBSPOT_DEFINED',
                    association_type_id=1  # Primary company association
                )]
            )
            logger.info(f"Associated contact {contact_id} with company {company_id}")
            
        except Exception as e:
            logger.warning(f"Failed to associate contact with company: {e}")
    
    def update(self, contact_id: str, properties: Dict) -> Optional[Dict]:
        """
        Update a contact
        
        Args:
            contact_id: HubSpot contact ID
            properties: Properties to update
        
        Returns:
            Updated contact dict or None
        """
        try:
            from hubspot.crm.contacts import SimplePublicObjectInput
            
            input_obj = SimplePublicObjectInput(properties=properties)
            response = self.client.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=input_obj
            )
            
            logger.info(f"Updated contact {contact_id}")
            
            return {
                'id': response.id,
                'properties': response.properties,
                'created_at': response.created_at,
                'updated_at': response.updated_at
            }
            
        except ApiException as e:
            logger.error(f"Error updating contact {contact_id}: {e}")
            return None
    
    def find_or_create(self, email: str, firstname: str = None, lastname: str = None,
                       company_id: str = None, properties: Dict = None) -> Optional[Dict]:
        """
        Find existing contact or create new one
        
        Args:
            email: Contact email
            firstname: First name
            lastname: Last name
            company_id: HubSpot company ID to associate with
            properties: Additional properties
        
        Returns:
            Contact dict (existing or new)
        """
        # First try to find by email
        existing = self.find_by_email(email)
        if existing:
            logger.info(f"Found existing contact for {email}: {existing['id']}")
            
            # Associate with company if not already
            if company_id:
                self._associate_with_company(existing['id'], company_id)
            
            return existing
        
        # Create new
        return self.create(email, firstname, lastname, properties, company_id)
