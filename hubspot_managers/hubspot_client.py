#!/usr/bin/env python3
"""
HubSpot API Client
Handles authentication and all API calls to HubSpot CRM

IMPORTANT NOTES:
- Many contacts have full name in 'firstname' field with 'lastname' = None
- Company names may have variations (e.g., "Agri Direct" vs "Agridirect.ie")
- Use fuzzy matching methods (find_contact_by_name, find_company_by_name) for searches
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from difflib import SequenceMatcher
import requests
from hubspot import HubSpot
from hubspot.crm.contacts import ApiException as ContactsApiException
from hubspot.crm.companies import ApiException as CompaniesApiException
from hubspot.crm.deals import ApiException as DealsApiException

logger = logging.getLogger(__name__)


class HubSpotClient:
    """Wrapper for HubSpot API with rate limiting and error handling"""
    
    def __init__(self, access_token: str, rate_limit: int = 10):
        """
        Initialize HubSpot client
        
        Args:
            access_token: Private App access token
            rate_limit: Max requests per second
        """
        self.access_token = access_token
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.client = HubSpot(access_token=access_token)
        
        logger.info("HubSpot client initialized")
    
    def _rate_limit(self):
        """Enforce rate limiting between API calls"""
        elapsed = time.time() - self.last_request_time
        min_interval = 1.0 / self.rate_limit
        
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        
        self.last_request_time = time.time()
    
    def _retry_request(self, func, *args, max_retries=3, **kwargs):
        """
        Retry a request with exponential backoff
        
        Args:
            func: Function to call
            max_retries: Maximum number of retries
        
        Returns:
            Result from function call
        """
        for attempt in range(max_retries):
            try:
                self._rate_limit()
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Max retries reached: {e}")
                    raise
                
                wait_time = 2 ** attempt
                logger.warning(f"Request failed, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
    
    # ==================== CONTACTS ====================
    
    def get_all_contacts(self, properties: List[str] = None) -> List[Dict]:
        """
        Fetch all contacts with pagination
        
        Args:
            properties: List of properties to fetch
        
        Returns:
            List of contact dictionaries
        """
        logger.info("Fetching all contacts...")
        
        contacts = []
        after = None
        
        default_properties = [
            'firstname', 'lastname', 'email', 'phone', 'company',
            'jobtitle', 'lifecyclestage', 'hs_lead_status',
            'createdate', 'lastmodifieddate', 'hubspot_owner_id'
        ]
        
        props = properties if properties else default_properties
        
        while True:
            try:
                response = self._retry_request(
                    self.client.crm.contacts.basic_api.get_page,
                    limit=100,
                    properties=props,
                    after=after
                )
                
                contacts.extend([self._contact_to_dict(c) for c in response.results])
                
                if not response.paging or not response.paging.next:
                    break
                
                after = response.paging.next.after
                logger.info(f"Fetched {len(contacts)} contacts so far...")
                
            except ContactsApiException as e:
                logger.error(f"Error fetching contacts: {e}")
                break
        
        logger.info(f"Fetched {len(contacts)} total contacts")
        return contacts
    
    def get_contact(self, contact_id: str, properties: List[str] = None) -> Optional[Dict]:
        """
        Fetch a single contact by ID
        
        Args:
            contact_id: HubSpot contact ID
            properties: List of properties to fetch
        
        Returns:
            Contact dictionary or None
        """
        try:
            response = self._retry_request(
                self.client.crm.contacts.basic_api.get_by_id,
                contact_id=contact_id,
                properties=properties
            )
            return self._contact_to_dict(response)
        except ContactsApiException as e:
            logger.error(f"Error fetching contact {contact_id}: {e}")
            return None
    
    def _contact_to_dict(self, contact) -> Dict:
        """Convert contact object to dictionary"""
        return {
            'id': contact.id,
            'properties': contact.properties,
            'created_at': contact.created_at,
            'updated_at': contact.updated_at,
            'archived': contact.archived
        }
    
    # ==================== COMPANIES ====================
    
    def get_all_companies(self, properties: List[str] = None) -> List[Dict]:
        """
        Fetch all companies with pagination
        
        Args:
            properties: List of properties to fetch
        
        Returns:
            List of company dictionaries
        """
        logger.info("Fetching all companies...")
        
        companies = []
        after = None
        
        default_properties = [
            'name', 'domain', 'industry', 'city', 'state', 'country',
            'phone', 'numberofemployees', 'annualrevenue',
            'createdate', 'hs_lastmodifieddate', 'hubspot_owner_id'
        ]
        
        props = properties if properties else default_properties
        
        while True:
            try:
                response = self._retry_request(
                    self.client.crm.companies.basic_api.get_page,
                    limit=100,
                    properties=props,
                    after=after
                )
                
                companies.extend([self._company_to_dict(c) for c in response.results])
                
                if not response.paging or not response.paging.next:
                    break
                
                after = response.paging.next.after
                logger.info(f"Fetched {len(companies)} companies so far...")
                
            except CompaniesApiException as e:
                logger.error(f"Error fetching companies: {e}")
                break
        
        logger.info(f"Fetched {len(companies)} total companies")
        return companies
    
    def get_company(self, company_id: str, properties: List[str] = None) -> Optional[Dict]:
        """
        Fetch a single company by ID
        
        Args:
            company_id: HubSpot company ID
            properties: List of properties to fetch
        
        Returns:
            Company dictionary or None
        """
        try:
            response = self._retry_request(
                self.client.crm.companies.basic_api.get_by_id,
                company_id=company_id,
                properties=properties
            )
            return self._company_to_dict(response)
        except CompaniesApiException as e:
            logger.error(f"Error fetching company {company_id}: {e}")
            return None
    
    def _company_to_dict(self, company) -> Dict:
        """Convert company object to dictionary"""
        return {
            'id': company.id,
            'properties': company.properties,
            'created_at': company.created_at,
            'updated_at': company.updated_at,
            'archived': company.archived
        }
    
    def search_companies(self, filters: Dict = None, properties: List[str] = None) -> List[Dict]:
        """
        Search companies with custom filters
        
        Args:
            filters: Dictionary of filter criteria (e.g., {'post_source': 'l15'})
            properties: List of properties to fetch
        
        Returns:
            List of company dictionaries matching the filters
        """
        try:
            from hubspot.crm.companies import PublicObjectSearchRequest
            
            # Build filter groups
            filter_groups = []
            if filters:
                filters_list = []
                for property_name, value in filters.items():
                    filters_list.append({
                        'propertyName': property_name,
                        'operator': 'EQ',
                        'value': str(value)
                    })
                filter_groups = [{'filters': filters_list}]
            
            search_request = PublicObjectSearchRequest(
                filter_groups=filter_groups,
                properties=properties or ['name', 'domain', 'post_source', 'post_track'],
                limit=100
            )
            
            response = self._retry_request(
                self.client.crm.companies.search_api.do_search,
                public_object_search_request=search_request
            )
            
            return [self._company_to_dict(c) for c in response.results]
        except Exception as e:
            logger.error(f"Error searching companies: {e}")
            return []
    
    def get_company_deals(self, company_id: str, properties: List[str] = None) -> List[Dict]:
        """
        Get all deals associated with a company
        
        Args:
            company_id: HubSpot company ID
            properties: List of deal properties to fetch
        
        Returns:
            List of deal dictionaries
        """
        try:
            # Get deal associations
            response = self._retry_request(
                self.client.crm.companies.associations_api.get_all,
                company_id=company_id,
                to_object_type='deals'
            )
            
            deal_ids = [assoc.to_object_id for assoc in response.results]
            
            # Fetch each deal's details
            deals = []
            for deal_id in deal_ids:
                deal = self.get_deal(deal_id, properties=properties)
                if deal:
                    deals.append(deal)
            
            return deals
        except Exception as e:
            logger.debug(f"Error fetching deals for company {company_id}: {e}")
            return []
    
    # ==================== DEALS ====================
    
    def get_all_deals(self, properties: List[str] = None) -> List[Dict]:
        """
        Fetch all deals with pagination
        
        Args:
            properties: List of properties to fetch
        
        Returns:
            List of deal dictionaries
        """
        logger.info("Fetching all deals...")
        
        deals = []
        after = None
        
        default_properties = [
            'dealname', 'amount', 'dealstage', 'pipeline', 'closedate',
            'createdate', 'hs_lastmodifieddate', 'dealtype',
            'description', 'hubspot_owner_id'
        ]
        
        props = properties if properties else default_properties
        
        while True:
            try:
                response = self._retry_request(
                    self.client.crm.deals.basic_api.get_page,
                    limit=100,
                    properties=props,
                    after=after
                )
                
                deals.extend([self._deal_to_dict(d) for d in response.results])
                
                if not response.paging or not response.paging.next:
                    break
                
                after = response.paging.next.after
                logger.info(f"Fetched {len(deals)} deals so far...")
                
            except DealsApiException as e:
                logger.error(f"Error fetching deals: {e}")
                break
        
        logger.info(f"Fetched {len(deals)} total deals")
        return deals
    
    def get_deal(self, deal_id: str, properties: List[str] = None) -> Optional[Dict]:
        """
        Fetch a single deal by ID
        
        Args:
            deal_id: HubSpot deal ID
            properties: List of properties to fetch
        
        Returns:
            Deal dictionary or None
        """
        try:
            response = self._retry_request(
                self.client.crm.deals.basic_api.get_by_id,
                deal_id=deal_id,
                properties=properties
            )
            return self._deal_to_dict(response)
        except DealsApiException as e:
            logger.error(f"Error fetching deal {deal_id}: {e}")
            return None
    
    def _deal_to_dict(self, deal) -> Dict:
        """Convert deal object to dictionary"""
        return {
            'id': deal.id,
            'properties': deal.properties,
            'created_at': deal.created_at,
            'updated_at': deal.updated_at,
            'archived': deal.archived
        }
    
    # ==================== ASSOCIATIONS ====================
    
    def get_contact_associations(self, contact_id: str) -> Dict[str, List[str]]:
        """
        Get all associations for a contact (companies, deals, etc.)
        
        Args:
            contact_id: HubSpot contact ID
        
        Returns:
            Dictionary mapping association type to list of IDs
        """
        associations = {
            'companies': [],
            'deals': [],
            'notes': [],
            'emails': [],
            'meetings': [],
            'calls': []
        }
        
        try:
            # Get associated companies
            response = self._retry_request(
                self.client.crm.contacts.associations_api.get_all,
                contact_id=contact_id,
                to_object_type='companies'
            )
            associations['companies'] = [assoc.to_object_id for assoc in response.results]
        except Exception as e:
            logger.debug(f"No company associations for contact {contact_id}: {e}")
        
        try:
            # Get associated deals
            response = self._retry_request(
                self.client.crm.contacts.associations_api.get_all,
                contact_id=contact_id,
                to_object_type='deals'
            )
            associations['deals'] = [assoc.to_object_id for assoc in response.results]
        except Exception as e:
            logger.debug(f"No deal associations for contact {contact_id}: {e}")
        
        return associations
    
    def get_company_associations(self, company_id: str) -> Dict[str, List[str]]:
        """
        Get all associations for a company (contacts, deals)
        
        Args:
            company_id: HubSpot company ID
        
        Returns:
            Dictionary mapping association type to list of IDs
        """
        associations = {
            'contacts': [],
            'deals': []
        }
        
        try:
            # Get associated contacts
            response = self._retry_request(
                self.client.crm.companies.associations_api.get_all,
                company_id=company_id,
                to_object_type='contacts'
            )
            associations['contacts'] = [assoc.to_object_id for assoc in response.results]
        except Exception as e:
            logger.debug(f"No contact associations for company {company_id}: {e}")
        
        try:
            # Get associated deals
            response = self._retry_request(
                self.client.crm.companies.associations_api.get_all,
                company_id=company_id,
                to_object_type='deals'
            )
            associations['deals'] = [assoc.to_object_id for assoc in response.results]
        except Exception as e:
            logger.debug(f"No deal associations for company {company_id}: {e}")
        
        return associations
    
    def get_deal_associations(self, deal_id: str, to_object_type: str = 'contacts') -> List[Dict]:
        """
        Get associations for a deal (contacts, companies)
        
        Args:
            deal_id: HubSpot deal ID
            to_object_type: Type of objects to get ('contacts' or 'companies')
        
        Returns:
            List of association dictionaries with 'id' field
        """
        try:
            response = self._retry_request(
                self.client.crm.deals.associations_api.get_all,
                deal_id=deal_id,
                to_object_type=to_object_type
            )
            return [{'id': assoc.to_object_id} for assoc in response.results]
        except Exception as e:
            logger.debug(f"No {to_object_type} associations for deal {deal_id}: {e}")
            return []
    
    # ==================== ENGAGEMENTS (EMAILS, NOTES, etc.) ====================
    
    def get_contact_emails(self, contact_id: str) -> List[Dict]:
        """
        Get all emails associated with a contact
        
        Args:
            contact_id: HubSpot contact ID
        
        Returns:
            List of email dictionaries
        """
        emails = []
        
        try:
            # Get email associations
            response = self._retry_request(
                self.client.crm.contacts.associations_api.get_all,
                contact_id=contact_id,
                to_object_type='emails'
            )
            
            email_ids = [assoc.to_object_id for assoc in response.results]
            
            # Fetch each email's details
            for email_id in email_ids:
                email = self.get_email(email_id)
                if email:
                    emails.append(email)
            
        except Exception as e:
            logger.debug(f"Error fetching emails for contact {contact_id}: {e}")
        
        return emails
    
    def get_email(self, email_id: str) -> Optional[Dict]:
        """
        Get a specific email by ID
        
        Args:
            email_id: HubSpot email engagement ID
        
        Returns:
            Email dictionary or None
        """
        try:
            # Use the engagements API v1 for emails
            url = f"https://api.hubapi.com/engagements/v1/engagements/{email_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            
            # Extract email metadata and content
            engagement = data.get('engagement', {})
            metadata = data.get('metadata', {})
            
            return {
                'id': email_id,
                'type': engagement.get('type'),
                'timestamp': engagement.get('timestamp'),
                'created_at': engagement.get('createdAt'),
                'owner_id': engagement.get('ownerId'),
                'subject': metadata.get('subject', ''),
                'from_email': metadata.get('from', {}).get('email', ''),
                'from_name': metadata.get('from', {}).get('firstName', '') + ' ' + metadata.get('from', {}).get('lastName', ''),
                'to': metadata.get('to', []),
                'cc': metadata.get('cc', []),
                'bcc': metadata.get('bcc', []),
                'text': metadata.get('text', ''),
                'html': metadata.get('html', ''),
                'status': metadata.get('status', ''),
                'attachments': metadata.get('attachments', [])
            }
            
        except Exception as e:
            logger.error(f"Error fetching email {email_id}: {e}")
            return None
    
    def get_all_emails(self) -> List[Dict]:
        """
        Fetch all emails from HubSpot
        
        Returns:
            List of email dictionaries
        """
        logger.info("Fetching all emails...")
        emails = []
        
        try:
            # Use engagements API to get all emails
            url = "https://api.hubapi.com/engagements/v1/engagements/paged"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            offset = 0
            has_more = True
            
            while has_more:
                self._rate_limit()
                params = {
                    'limit': 250,
                    'offset': offset
                }
                
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                # Filter for email engagements only
                for result in data.get('results', []):
                    engagement = result.get('engagement', {})
                    if engagement.get('type') == 'EMAIL':
                        metadata = result.get('metadata', {})
                        associations = result.get('associations', {})
                        
                        emails.append({
                            'id': engagement.get('id'),
                            'type': engagement.get('type'),
                            'timestamp': engagement.get('timestamp'),
                            'created_at': engagement.get('createdAt'),
                            'owner_id': engagement.get('ownerId'),
                            'subject': metadata.get('subject', ''),
                            'from_email': metadata.get('from', {}).get('email', ''),
                            'from_name': metadata.get('from', {}).get('firstName', '') + ' ' + metadata.get('from', {}).get('lastName', ''),
                            'to': metadata.get('to', []),
                            'cc': metadata.get('cc', []),
                            'bcc': metadata.get('bcc', []),
                            'text': metadata.get('text', ''),
                            'html': metadata.get('html', ''),
                            'status': metadata.get('status', ''),
                            'attachments': metadata.get('attachments', []),
                            'contact_ids': associations.get('contactIds', []),
                            'company_ids': associations.get('companyIds', []),
                            'deal_ids': associations.get('dealIds', [])
                        })
                
                has_more = data.get('hasMore', False)
                offset = data.get('offset', 0)
                
                logger.info(f"Fetched {len(emails)} emails so far...")
            
        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
        
        logger.info(f"Fetched {len(emails)} total emails")
        return emails
    
    def get_contact_notes(self, contact_id: str) -> List[Dict]:
        """
        Get all notes associated with a contact
        
        Args:
            contact_id: HubSpot contact ID
        
        Returns:
            List of note dictionaries
        """
        notes = []
        
        try:
            # Get note associations
            response = self._retry_request(
                self.client.crm.contacts.associations_api.get_all,
                contact_id=contact_id,
                to_object_type='notes'
            )
            
            note_ids = [assoc.to_object_id for assoc in response.results]
            
            # Fetch each note's details
            for note_id in note_ids:
                note = self.get_note(note_id)
                if note:
                    notes.append(note)
            
        except Exception as e:
            logger.debug(f"Error fetching notes for contact {contact_id}: {e}")
        
        return notes
    
    def get_note(self, note_id: str) -> Optional[Dict]:
        """
        Get a specific note by ID
        
        Args:
            note_id: HubSpot note ID
        
        Returns:
            Note dictionary or None
        """
        try:
            response = self._retry_request(
                self.client.crm.objects.notes.basic_api.get_by_id,
                note_id=note_id,
                properties=['hs_note_body', 'hs_timestamp', 'hubspot_owner_id']
            )
            
            return {
                'id': response.id,
                'body': response.properties.get('hs_note_body', ''),
                'timestamp': response.properties.get('hs_timestamp', ''),
                'owner_id': response.properties.get('hubspot_owner_id', ''),
                'created_at': response.created_at,
                'updated_at': response.updated_at
            }
            
        except Exception as e:
            logger.error(f"Error fetching note {note_id}: {e}")
            return None
    
    # ==================== CUSTOM OBJECTS ====================
    
    def create_custom_object_schema(self, schema_definition: Dict) -> Optional[Dict]:
        """
        Create a custom object schema
        
        Args:
            schema_definition: Dictionary containing object schema
                Required fields:
                - name: object name
                - labels: {singular, plural}
                - primaryDisplayProperty: main property
                - properties: list of property definitions
                - associatedObjects: list of objects to associate with
        
        Returns:
            Created schema dictionary or None
        """
        try:
            url = "https://api.hubapi.com/crm/v3/schemas"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.post(url, headers=headers, json=schema_definition)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Custom object schema created: {data.get('name')} (ID: {data.get('id')})")
            return data
            
        except Exception as e:
            logger.error(f"Error creating custom object schema: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
    
    def get_custom_object_schema(self, object_type: str) -> Optional[Dict]:
        """
        Get a custom object schema
        
        Args:
            object_type: Object type ID or fully qualified name
        
        Returns:
            Schema dictionary or None
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/schemas/{object_type}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching custom object schema: {e}")
            return None
    
    def update_custom_object_schema(self, object_type: str, updates: Dict) -> Optional[Dict]:
        """
        Update a custom object schema
        
        Args:
            object_type: Object type ID or fully qualified name
            updates: Dictionary with fields to update
        
        Returns:
            Updated schema dictionary or None
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/schemas/{object_type}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.patch(url, headers=headers, json=updates)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Custom object schema updated: {object_type}")
            return data
            
        except Exception as e:
            logger.error(f"Error updating custom object schema: {e}")
            return None
    
    def create_custom_object_record(self, object_type: str, properties: Dict) -> Optional[Dict]:
        """
        Create a custom object record
        
        Args:
            object_type: Object type ID or name
            properties: Dictionary of property values
        
        Returns:
            Created record dictionary or None
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/{object_type}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            payload = {"properties": properties}
            
            self._rate_limit()
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Custom object record created: {object_type} (ID: {data.get('id')})")
            return data
            
        except Exception as e:
            logger.error(f"Error creating custom object record: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
    
    def update_custom_object_record(self, object_type: str, record_id: str, properties: Dict) -> Optional[Dict]:
        """
        Update a custom object record
        
        Args:
            object_type: Object type ID or name
            record_id: Record ID to update
            properties: Dictionary of property values to update
        
        Returns:
            Updated record dictionary or None
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/{object_type}/{record_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            payload = {"properties": properties}
            
            self._rate_limit()
            response = requests.patch(url, headers=headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Custom object record updated: {object_type}/{record_id}")
            return data
            
        except Exception as e:
            logger.error(f"Error updating custom object record: {e}")
            return None
    
    def get_custom_object_record(self, object_type: str, record_id: str, properties: List[str] = None) -> Optional[Dict]:
        """
        Get a custom object record by ID
        
        Args:
            object_type: Object type ID or name
            record_id: Record ID
            properties: Optional list of properties to retrieve
        
        Returns:
            Record dictionary or None
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/{object_type}/{record_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            params = {}
            if properties:
                params['properties'] = ','.join(properties)
            
            self._rate_limit()
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching custom object record: {e}")
            return None
    
    def get_custom_object_records(self, object_type: str, properties: List[str] = None, limit: int = 100) -> List[Dict]:
        """
        Get all custom object records with pagination
        
        Args:
            object_type: Object type ID or name
            properties: Optional list of properties to retrieve
            limit: Records per page
        
        Returns:
            List of record dictionaries
        """
        logger.info(f"Fetching custom object records: {object_type}...")
        
        records = []
        after = None
        
        while True:
            try:
                url = f"https://api.hubapi.com/crm/v3/objects/{object_type}"
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json"
                }
                
                params = {'limit': limit}
                if properties:
                    params['properties'] = ','.join(properties)
                if after:
                    params['after'] = after
                
                self._rate_limit()
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                records.extend(data.get('results', []))
                
                paging = data.get('paging', {})
                if not paging.get('next'):
                    break
                
                after = paging['next'].get('after')
                logger.info(f"Fetched {len(records)} records so far...")
                
            except Exception as e:
                logger.error(f"Error fetching custom object records: {e}")
                break
        
        logger.info(f"Fetched {len(records)} total records")
        return records
    
    def create_custom_object_association(self, from_object_type: str, from_record_id: str, 
                                        to_object_type: str, to_record_id: str, 
                                        association_type_id: str = None) -> bool:
        """
        Create an association between a custom object record and another record
        
        Args:
            from_object_type: Source object type
            from_record_id: Source record ID
            to_object_type: Target object type (e.g., 'contacts', 'companies', 'deals')
            to_record_id: Target record ID
            association_type_id: Optional association type ID
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # If no association type provided, use default (first available)
            if not association_type_id:
                # Get schema to find association type
                schema = self.get_custom_object_schema(from_object_type)
                if schema and 'associations' in schema:
                    for assoc in schema['associations']:
                        if assoc.get('toObjectTypeId') == to_object_type or assoc.get('name', '').startswith(to_object_type):
                            association_type_id = assoc.get('id')
                            break
            
            if not association_type_id:
                logger.error(f"Could not determine association type ID")
                return False
            
            url = f"https://api.hubapi.com/crm/v3/objects/{from_object_type}/{from_record_id}/associations/{to_object_type}/{to_record_id}/{association_type_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.put(url, headers=headers)
            response.raise_for_status()
            
            logger.info(f"Association created: {from_object_type}/{from_record_id} → {to_object_type}/{to_record_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating association: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return False
    
    def delete_custom_object_record(self, object_type: str, record_id: str) -> bool:
        """
        Delete a custom object record
        
        Args:
            object_type: Object type ID or name
            record_id: Record ID to delete
        
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/{object_type}/{record_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.delete(url, headers=headers)
            response.raise_for_status()
            
            logger.info(f"Custom object record deleted: {object_type}/{record_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting custom object record: {e}")
            return False
    
    def get_portal_id(self) -> Optional[int]:
        """
        Get the HubSpot portal (account) ID
        
        Returns:
            Portal ID or None
        """
        try:
            url = "https://api.hubapi.com/account-info/v3/details"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            self._rate_limit()
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            portal_id = data.get('portalId')
            logger.info(f"Portal ID: {portal_id}")
            return portal_id
            
        except Exception as e:
            logger.error(f"Error fetching portal ID: {e}")
            return None
    
    # ==================== UTILITY METHODS ====================
    
    def test_connection(self) -> bool:
        """
        Test if the HubSpot connection is working
        
        Returns:
            True if connection is successful, False otherwise
        """
        try:
            # Try to fetch contacts (with limit 1 for quick test)
            response = self._retry_request(
                self.client.crm.contacts.basic_api.get_page,
                limit=1
            )
            
            logger.info("HubSpot connection test successful")
            logger.info(f"Successfully authenticated - able to access CRM data")
            return True
            
        except Exception as e:
            logger.error(f"HubSpot connection test failed: {e}")
            return False
    
    # ==================== FUZZY SEARCH UTILITIES ====================
    
    @staticmethod
    def _normalize_string(s: str) -> str:
        """Normalize string for comparison"""
        return s.lower().strip() if s else ""
    
    @staticmethod
    def _similarity_ratio(a: str, b: str) -> float:
        """Calculate similarity ratio between two strings (0.0 to 1.0)"""
        if not a or not b:
            return 0.0
        norm_a = HubSpotClient._normalize_string(a)
        norm_b = HubSpotClient._normalize_string(b)
        return SequenceMatcher(None, norm_a, norm_b).ratio()
    
    def find_contact_by_name(self, first_name: str, last_name: str, 
                            company_name: Optional[str] = None,
                            similarity_threshold: float = 0.85) -> List[Dict]:
        """
        Find contacts by name with fuzzy matching
        
        IMPORTANT: Many contacts in HubSpot have their full name in the 'firstname' 
        field with 'lastname' = None. This method handles both cases.
        
        Args:
            first_name: First name to search for
            last_name: Last name to search for (can include initials like "M.")
            company_name: Optional company name to filter results
            similarity_threshold: Minimum similarity score (0.0 to 1.0)
        
        Returns:
            List of matching contact dictionaries, sorted by similarity score
        """
        logger.info(f"Searching for contact: {first_name} {last_name}")
        
        # Fetch all contacts
        contacts = self.get_all_contacts(properties=['firstname', 'lastname', 'email', 'company', 'jobtitle'])
        
        matches = []
        norm_first = self._normalize_string(first_name)
        norm_last = self._normalize_string(last_name)
        full_name_search = f"{norm_first} {norm_last}".strip()
        
        for contact in contacts:
            props = contact.get('properties', {})
            first = self._normalize_string(props.get('firstname', ''))
            last = self._normalize_string(props.get('lastname', ''))
            
            if not first:
                continue
            
            similarity = 0.0
            match_type = None
            
            # CASE 1: Full name in firstname field (lastname is None/empty)
            if not last:
                full_name_in_crm = first
                ratio = self._similarity_ratio(full_name_in_crm, full_name_search)
                
                if ratio >= similarity_threshold:
                    similarity = ratio
                    match_type = "full_name_in_firstname"
                elif norm_first in full_name_in_crm and norm_last in full_name_in_crm:
                    similarity = 0.9  # High score for contains match
                    match_type = "contains_both_names"
            
            # CASE 2: Normal split first/last names
            else:
                first_ratio = self._similarity_ratio(first, norm_first)
                
                # Handle initials in last name (e.g., "M.")
                if len(norm_last) <= 2 and '.' in last_name:
                    last_ratio = 0.95 if (last and last[0] == norm_last[0]) else 0.0
                else:
                    last_ratio = self._similarity_ratio(last, norm_last)
                
                avg_ratio = (first_ratio + last_ratio) / 2
                
                if avg_ratio >= similarity_threshold:
                    similarity = avg_ratio
                    match_type = "split_names"
            
            # If we have a match, check company if specified
            if similarity > 0:
                if company_name:
                    contact_company = self._normalize_string(props.get('company', ''))
                    norm_company = self._normalize_string(company_name)
                    
                    # Check if companies match (fuzzy)
                    company_matches = (
                        self._similarity_ratio(contact_company, norm_company) > 0.7 or
                        norm_company in contact_company or
                        contact_company in norm_company
                    )
                    
                    if not company_matches:
                        continue
                
                matches.append({
                    'contact': contact,
                    'similarity': similarity,
                    'match_type': match_type,
                    'name': f"{props.get('firstname', '')} {props.get('lastname', '')}".strip(),
                    'email': props.get('email', 'N/A'),
                    'company': props.get('company', 'N/A')
                })
        
        # Sort by similarity
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        
        if matches:
            logger.info(f"Found {len(matches)} matching contacts")
        else:
            logger.info(f"No matching contacts found")
        
        return matches
    
    def find_company_by_name(self, company_name: str, 
                            similarity_threshold: float = 0.80) -> List[Dict]:
        """
        Find companies by name with fuzzy matching
        
        Handles variations like:
        - "Agridirect.ie" vs "Agri Direct"
        - "MOTHER Denim" vs "Mother Denim"
        - "DVF" vs "Diane von Furstenberg"
        
        Args:
            company_name: Company name to search for
            similarity_threshold: Minimum similarity score (0.0 to 1.0)
        
        Returns:
            List of matching company dictionaries, sorted by similarity score
        """
        logger.info(f"Searching for company: {company_name}")
        
        # Fetch all companies
        companies = self.get_all_companies(properties=['name', 'domain', 'website'])
        
        matches = []
        norm_search = self._normalize_string(company_name)
        
        # Common company name variations
        special_mappings = {
            'agridirect.ie': ['agri direct', 'agridirect'],
            'agridirect': ['agri direct'],
            'dvf': ['diane von furstenberg', 'vf'],
            'mother denim': ['mother'],
            'dr. squatch': ['dr squatch'],
            'drm-lnd': ['drm lnd'],
        }
        
        # Get possible variants for search term
        search_variants = [norm_search]
        for key, variants in special_mappings.items():
            if key in norm_search or norm_search in key:
                search_variants.extend(variants)
        
        for company in companies:
            name = company['properties'].get('name', '')
            if not name:
                continue
            
            norm_name = self._normalize_string(name)
            
            # Calculate best similarity across all variants
            best_similarity = 0.0
            
            for variant in search_variants:
                # Direct similarity
                ratio = self._similarity_ratio(variant, norm_name)
                best_similarity = max(best_similarity, ratio)
                
                # Contains check
                if variant in norm_name or norm_name in variant:
                    best_similarity = max(best_similarity, 0.9)
            
            if best_similarity >= similarity_threshold:
                matches.append({
                    'company': company,
                    'similarity': best_similarity,
                    'name': name,
                    'domain': company['properties'].get('domain', 'N/A'),
                    'id': company['id']
                })
        
        # Sort by similarity
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        
        if matches:
            logger.info(f"Found {len(matches)} matching companies")
        else:
            logger.info(f"No matching companies found")
        
        return matches

