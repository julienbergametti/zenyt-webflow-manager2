"""
Prospect Manager for creating prospect folder structure and files
Creates prospect folders matching zenyt_sales/prospects/ format
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


def normalize_company_name_for_folder(company_name: str) -> str:
    """
    Normalize company name for folder naming (PascalCase with underscores)
    Examples: "Picture Organic Clothing" -> "Picture_Organic_Clothing"
    """
    if not company_name:
        return "Unknown"
    
    # Replace special characters with spaces
    name = company_name.replace('-', ' ').replace('_', ' ')
    # Split by spaces and capitalize each word
    words = [word.capitalize() for word in name.split() if word]
    # Join with underscores
    return '_'.join(words)


def normalize_contact_name_for_file(contact_firstname: str, contact_lastname: str) -> str:
    """
    Normalize contact name for file naming (lowercase with underscores)
    Examples: "Antoine Caillet" -> "antoine_caillet"
    """
    first = (contact_firstname or "").strip().lower()
    last = (contact_lastname or "").strip().lower()
    
    # Handle full name in firstname field
    if not last and ' ' in first:
        parts = first.split()
        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""
    
    if first and last:
        return f"{first}_{last}"
    elif first:
        return first
    else:
        return "contact"


def create_prospect_folder(prospects_base_path: Path, company_name: str) -> Path:
    """
    Create prospect folder structure
    Returns: Path to created folder
    """
    folder_name = normalize_company_name_for_folder(company_name)
    folder_path = prospects_base_path / folder_name
    
    # Create folder if it doesn't exist
    folder_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created/verified prospect folder: {folder_path}")
    
    return folder_path


def create_campaign_overview(
    folder_path: Path,
    company_name: str,
    company_domain: str,
    contact_name: str,
    contact_email: str,
    website_url: str,
    industry: Optional[str] = None,
    is_agency: bool = False,
    agency_name: Optional[str] = None
) -> Path:
    """
    Create _CAMPAIGN_OVERVIEW.md file
    Returns: Path to created file
    """
    file_path = folder_path / "_CAMPAIGN_OVERVIEW.md"
    
    # Determine language
    language = "French 🇫🇷" if ".fr" in website_url.lower() or ".be" in website_url.lower() or ".ch" in website_url.lower() else "English 🇬🇧"
    
    # Build contact info
    contact_line = f"{contact_name} ({contact_email})"
    if is_agency and agency_name:
        contact_line += f" - Agency: {agency_name}"
    
    content = f"""# {company_name} - Campaign Overview

**Campaign Start**: {datetime.now().strftime('%B %d, %Y')}  
**Status**: Warm (Demo Request)  
**Priority**: B-Tier  
**Contact**: {contact_line}  
**Language**: {language}

---

## 🎯 TL;DR

### Current Situation
• Demo request received for {website_url}
{f"• Agency contact: {contact_name} from {agency_name} (email domain differs from site)" if is_agency else "• Direct contact"}
• {industry or "E-commerce"} e-commerce with product information focus
{"• Need to determine if evaluating for client specifically or portfolio-wide" if is_agency else "• Standard demo request sequence"}

### Immediate Action
Send demo request Touch 1 email{" with agency portfolio question" if is_agency else ""}, launch analysis on {website_url}

---

## 🏢 Company Profile

**Industry**: {industry or "[To be determined]"}  
**Revenue**: [To be determined]  
**Catalog Size**: [To be determined]  
**Platform**: [To be determined]  
**Website**: {website_url}

### ICP Assessment (4-Check Framework)
| Criteria          | Status | Evidence |
| ----------------- | ------ | -------- |
| Revenue $10-500M? | ⏳ TBD  | Need to verify revenue |
| ≥50 SKUs?         | ⏳ TBD  | Need to verify catalog size |
| Monthly+ Updates? | ⏳ TBD  | Need to verify update frequency |
| Former Wholesale? | ⏳ TBD  | Need to verify distribution model |

**ICP Score**: 0/4 (TBD)  
**Priority**: B-tier (demo request received)

---

## 👤 Key Contacts

| Name | Role | Email | LinkedIn | Status |
| ---- | ---- | ----- | -------- | ------ |
| {contact_name} | {"Agency Contact" if is_agency else "Direct Contact"} | {contact_email} | | Demo Request Received |

{"**Note**: " + contact_name + " is an agency contact (" + agency_name + ") evaluating " + website_url + ". Key question: Is this for " + company_name + " specifically, or something to bring across the portfolio?" if is_agency else "**Note**: " + contact_name + " is a direct contact (email domain matches site). This is NOT an agency situation - standard demo request sequence."}

---

## 🔍 Key Findings

*Analysis pending - launching AI scan on {website_url}*

---

## 📅 Timeline

| Date | Event | Status |
| ---- | ----- | ------ |
| {datetime.now().strftime('%Y-%m-%d')} | Demo request received | ✅ |
| {datetime.now().strftime('%Y-%m-%d')} | Touch 1 email sent (demo request) | ⏳ Pending |

---

## ✅ Next Actions

### Immediate
1. Send Touch 1 demo request email{" with agency portfolio question" if is_agency else ""}
2. Launch AI analysis on {website_url}
{"3. Wait for response to determine if client-specific or portfolio evaluation" if is_agency else "3. Wait for response"}

### If Response
1. {"Tailor findings based on their answer (client-specific vs. portfolio)" if is_agency else "Schedule 30-minute demo call"}
2. Schedule 30-minute demo call
3. Prepare findings delivery (Touch 2)

### If No Response
1. Follow demo request sequence (Touch 2-10)
2. Use asset-driven follow-ups (case studies, external content)

---

## 📁 Files in This Folder

• `_CAMPAIGN_OVERVIEW.md` (this file)
• `{normalize_contact_name_for_file(contact_name.split()[0] if " " in contact_name else contact_name, contact_name.split()[-1] if " " in contact_name and len(contact_name.split()) > 1 else "")}_touch_1.md` (demo request email)

---

**Last Updated**: {datetime.now().strftime('%B %d, %Y')}  
**Owner**: Arthur
"""
    
    file_path.write_text(content, encoding='utf-8')
    logger.info(f"Created campaign overview: {file_path}")
    
    return file_path


def create_touch_1_file(
    folder_path: Path,
    contact_firstname: str,
    contact_lastname: str,
    contact_email: str,
    company_name: str,
    website_url: str,
    email_data: Dict,
    is_agency: bool = False,
    agency_name: Optional[str] = None
) -> Path:
    """
    Create {contact_name}_touch_1.md file
    Returns: Path to created file
    """
    contact_name_normalized = normalize_contact_name_for_file(contact_firstname, contact_lastname)
    file_path = folder_path / f"{contact_name_normalized}_touch_1.md"
    
    # Build contact info
    contact_full_name = f"{contact_firstname} {contact_lastname}".strip() or contact_email.split('@')[0]
    
    content = f"""# {contact_full_name} - Touch 1 (Demo Request)

**Date**: {datetime.now().strftime('%B %d, %Y')}  
**Type**: Demo Request (Inbound)  
**Status**: Ready to Send  
**Contact**: {contact_full_name} ({contact_email})  
{"**Agency**: " + agency_name if is_agency else "**Company**: " + company_name}  
**Website**: {website_url}

---

## Email

**[📧 Draft Email]({email_data['mailto_link']})**

**Subject**: {email_data['subject']}

**READY TO COPY:**

```
{email_data['body']}
```

---

## Context

**Template Used**: Demo Request Touch 1 (from `zenyt-docs/sales/inbound/pre-call-email-template.md`)

**Key Elements**:
{"- Agency detection: Email domain differs from site" if is_agency else "- Direct contact: Email domain matches site - NOT an agency situation"}
- {"Portfolio question included early in email" if is_agency else "No agency portfolio question needed"}
- Industry-specific: {email_data.get('industry', 'E-commerce')} challenges
- Discovery questions to tailor analysis
- Calendar link for demo call
{"- **" + email_data['language'].upper() + " language** (brand is " + email_data['language'] + ", contact is " + email_data['language'] + ")" if email_data['language'] != 'en' else ""}

**Word Count**: ~{email_data['word_count']} words

**Industry Context**: {company_name} is a {email_data.get('industry', 'e-commerce')} brand. Product information accuracy is critical for e-commerce conversion.

---

**Status**: Ready to Send  
**Next Touch**: Touch 2 (Post-Analysis Findings) - Send when AI analysis is complete
"""
    
    file_path.write_text(content, encoding='utf-8')
    logger.info(f"Created touch 1 file: {file_path}")
    
    return file_path
