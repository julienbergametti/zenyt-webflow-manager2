"""
Configuration settings for the Zenyt Lead Manager
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


class ICPSettings:
    """ICP (Ideal Customer Profile) scoring thresholds"""
    def __init__(self):
        # Minimum requirements
        self.min_employees = int(os.getenv('ICP_MIN_EMPLOYEES', '50'))
        self.min_revenue = int(os.getenv('ICP_MIN_REVENUE', '10000000'))  # $10M
        self.max_revenue = int(os.getenv('ICP_MAX_REVENUE', '500000000'))  # $500M
        self.min_sku_count = int(os.getenv('ICP_MIN_SKU_COUNT', '50'))
        
        # Priority score thresholds
        self.very_high_score = int(os.getenv('ICP_VERY_HIGH_SCORE', '80'))
        self.high_score = int(os.getenv('ICP_HIGH_SCORE', '60'))
        self.medium_score = int(os.getenv('ICP_MEDIUM_SCORE', '40'))
        self.low_score = int(os.getenv('ICP_LOW_SCORE', '20'))


class HubSpotSettings:
    """HubSpot configuration"""
    def __init__(self):
        self.access_token = os.getenv('HUBSPOT_ACCESS_TOKEN')


class WebflowSettings:
    """Webflow configuration"""
    def __init__(self):
        self.api_token = os.getenv('WEBFLOW_API_TOKEN')
        self.site_id = os.getenv('WEBFLOW_SITE_ID')


class ProspectSettings:
    """Prospect folder and email generation settings"""
    def __init__(self):
        # Path to prospects directory (relative to workspace root)
        workspace_root = Path(__file__).parent.parent.parent.parent
        self.prospects_base_path = Path(workspace_root) / "zenyt_sales" / "prospects"
        self.email_templates_path = Path(workspace_root) / "zenyt-docs" / "sales" / "inbound"
        
        # Form field names that indicate demo request
        self.demo_request_fields = [
            "demo_request",
            "request_type",
            "interested_in",
            "demo",
            "request_demo"
        ]


class Settings:
    """Main settings container"""
    def __init__(self):
        self.hubspot = HubSpotSettings()
        self.webflow = WebflowSettings()
        self.icp = ICPSettings()
        self.prospect = ProspectSettings()
    
    def validate(self):
        """Validate configuration and return list of errors"""
        errors = []
        
        if not self.hubspot.access_token:
            errors.append("HUBSPOT_ACCESS_TOKEN not set")
        
        if not self.webflow.api_token:
            errors.append("WEBFLOW_API_TOKEN not set")
            
        if not self.webflow.site_id:
            errors.append("WEBFLOW_SITE_ID not set")
        
        return errors


# Singleton instance
_settings = None


def get_settings() -> Settings:
    """Get the settings singleton"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
