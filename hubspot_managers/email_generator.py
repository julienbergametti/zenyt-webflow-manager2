"""
Email Generator for Demo Request Emails
Generates demo request emails using templates from zenyt-docs/sales/inbound/
"""
import logging
import re
from typing import Dict, Optional
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)


def extract_domain(url_or_email: str) -> str:
    """Extract domain from URL or email"""
    if not url_or_email:
        return ""
    
    # Remove protocol
    domain = re.sub(r'^https?://', '', url_or_email.lower())
    # Remove www.
    domain = re.sub(r'^www\.', '', domain)
    # Get just the domain part
    domain = domain.split('/')[0].split('?')[0].split('@')[-1]
    return domain.strip()


def detect_agency(contact_email: str, website_url: str) -> tuple:
    """
    Detect if contact is from an agency
    Returns: (is_agency, agency_name)
    """
    if not contact_email or not website_url:
        return False, None
    
    email_domain = extract_domain(contact_email)
    website_domain = extract_domain(website_url)
    
    # If domains are different, likely an agency
    if email_domain != website_domain:
        # Extract agency name from email domain
        agency_name = email_domain.split('.')[0].capitalize()
        return True, agency_name
    
    return False, None


def detect_language(website_url: str, company_country: Optional[str] = None) -> str:
    """
    Detect language based on website TLD or company country
    Returns: 'fr' for French, 'en' for English (default)
    """
    if not website_url:
        return "en"
    
    # Check TLD
    domain = extract_domain(website_url)
    tld = domain.split('.')[-1] if '.' in domain else ""
    
    # French TLDs
    if tld in ['fr', 'be', 'ch', 'lu', 'mc']:
        return "fr"
    
    # Danish/Nordic TLDs
    if tld in ['dk', 'no', 'se', 'fi']:
        return "en"  # Default to English for now, can add Danish later
    
    # Check company country
    if company_country:
        country_lower = company_country.lower()
        if any(c in country_lower for c in ['france', 'french', 'belgium', 'switzerland']):
            return "fr"
    
    return "en"


def get_industry_challenges(industry: Optional[str]) -> Dict[str, str]:
    """
    Get industry-specific challenges and P.S. line
    Returns: {challenges: str, ps_line: str}
    """
    industry_lower = (industry or "").lower()
    
    # Electronics/Networking
    if any(term in industry_lower for term in ['electronics', 'networking', 'technology', 'tech']):
        return {
            "challenges": "technical specifications accuracy, compatibility information consistency, and product variant accuracy (models, configurations)",
            "ps_line": "Many electronics brands tell us that technical specification accuracy and compatibility information are critical for customer trust and reducing returns on high-ticket purchases."
        }
    
    # Jewelry
    if any(term in industry_lower for term in ['jewelry', 'jewellery', 'watches', 'luxury']):
        return {
            "challenges": "materials accuracy (18kt gold plated, 925 sterling silver, lab-grown diamonds), sizing information consistency (ring sizes, chain lengths), and product specifications (dimensions, weight, stone details)",
            "ps_line": "Many jewelry brands tell us that materials accuracy and sizing information consistency are critical for customer trust and reducing returns on high-value purchases."
        }
    
    # Outdoor/Technical Apparel
    if any(term in industry_lower for term in ['outdoor', 'sport', 'ski', 'snowboard', 'apparel', 'clothing']):
        return {
            "challenges": "technical specifications accuracy (waterproof ratings, breathability, insulation), sizing information consistency (fit guides, size charts), and product specifications (materials, features, compatibility)",
            "ps_line": "Many outdoor brands tell us that technical specification accuracy and sizing consistency are critical for customer trust and reducing returns on high-ticket purchases."
        }
    
    # Eyewear
    if any(term in industry_lower for term in ['eyewear', 'optical', 'glasses', 'sunglasses']):
        return {
            "challenges": "product specifications accuracy (frame dimensions, lens options, prescription compatibility), sizing information consistency (fit guides, frame measurements), and prescription information accuracy (lens types, add-ons, compatibility)",
            "ps_line": "Many eyewear brands tell us that accurate frame specifications and prescription compatibility information are critical for customer trust and reducing returns on high-value purchases."
        }
    
    # Fashion
    if any(term in industry_lower for term in ['fashion', 'clothing', 'apparel', 'retail']):
        return {
            "challenges": "sizing accuracy, materials consistency, product specifications, and categorization",
            "ps_line": "Many fashion brands tell us that sizing accuracy and materials consistency are critical for customer trust and reducing returns."
        }
    
    # Default/E-commerce
    return {
        "challenges": "product information accuracy, specifications consistency, and categorization",
        "ps_line": "Many e-commerce brands tell us that product information accuracy is critical for customer trust and conversion."
    }


def generate_demo_request_email(
    contact_email: str,
    contact_firstname: str,
    contact_lastname: str,
    company_name: str,
    website_url: str,
    industry: Optional[str] = None,
    company_country: Optional[str] = None
) -> Dict:
    """
    Generate demo request email content
    
    Args:
        contact_email: Contact email address
        contact_firstname: First name
        contact_lastname: Last name
        company_name: Company name
        website_url: Website URL to analyze
        industry: Industry (optional)
        company_country: Company country (optional)
    
    Returns:
        Dict with subject, body, mailto_link, and metadata
    """
    # Detect agency
    is_agency, agency_name = detect_agency(contact_email, website_url)
    
    # Detect language
    language = detect_language(website_url, company_country)
    
    # Get industry challenges
    industry_info = get_industry_challenges(industry)
    
    # Extract website domain for subject
    website_domain = extract_domain(website_url)
    
    # Build contact name
    contact_name = f"{contact_firstname} {contact_lastname}".strip() or contact_email.split('@')[0]
    first_name = contact_firstname or contact_name.split()[0] if contact_name else "there"
    
    # Generate subject
    if language == "fr":
        subject = f"lancement de l'analyse sur {website_domain}"
    else:
        subject = f"launching analysis on {website_domain}"
    
    # Generate email body
    if language == "fr":
        body = generate_french_email(
            first_name, contact_email, company_name, website_domain,
            is_agency, agency_name, industry_info
        )
    else:
        body = generate_english_email(
            first_name, contact_email, company_name, website_domain,
            is_agency, agency_name, industry_info
        )
    
    # Generate mailto link
    mailto_subject = quote(subject)
    mailto_body = quote(body)
    mailto_link = f"mailto:{contact_email}?subject={mailto_subject}&body={mailto_body}"
    
    return {
        "subject": subject,
        "body": body,
        "mailto_link": mailto_link,
        "language": language,
        "is_agency": is_agency,
        "agency_name": agency_name,
        "industry": industry,
        "word_count": len(body.split())
    }


def generate_english_email(
    first_name: str,
    contact_email: str,
    company_name: str,
    website_domain: str,
    is_agency: bool,
    agency_name: Optional[str],
    industry_info: Dict
) -> str:
    """Generate English email body"""
    lines = [
        f"Hi {first_name},",
        "",
        f"Thanks for your request to start with Zenyt.ai on {website_domain}.",
        ""
    ]
    
    # Agency question if applicable
    if is_agency and agency_name:
        lines.extend([
            f"I see you're at {agency_name}. Given you're evaluating {website_domain}, I'm curious: are you exploring Zenyt specifically for this client, or as something to bring across your portfolio?",
            ""
        ])
    
    lines.extend([
        f"We're launching the AI analysis on {website_domain} today so I have live findings ready.",
        "",
        f"E-commerce brands have unique QA challenges: {industry_info['challenges']} all impact conversion and customer trust.",
        "",
        "Quick questions to help me tailor the analysis:",
        "• What conversion metrics are you focused on improving right now?",
        "• Are you seeing specific challenges with product information accuracy or consistency?",
    ])
    
    if is_agency:
        lines.append("• Would this be something you'd bring to other clients as well?")
    else:
        lines.append("• Is ensuring accurate product specifications a priority?")
    
    lines.extend([
        "",
        "Would you be available for a 30 minute call to walk through findings?",
        "",
        "Here's my calendar: https://calendly.com/arthur-pentecoste/zenyt-book-a-demo",
        "",
        "Best,",
        "Arthur",
        "",
        f"P.S. {industry_info['ps_line']}"
    ])
    
    return "\n".join(lines)


def generate_french_email(
    first_name: str,
    contact_email: str,
    company_name: str,
    website_domain: str,
    is_agency: bool,
    agency_name: Optional[str],
    industry_info: Dict
) -> str:
    """Generate French email body"""
    lines = [
        f"Bonjour {first_name},",
        "",
        f"Merci pour votre demande de démo sur {website_domain}.",
        ""
    ]
    
    # Agency question if applicable
    if is_agency and agency_name:
        lines.extend([
            f"Je vois que vous êtes chez {agency_name}. Vu que vous évaluez {website_domain}, je suis curieux : explorez-vous Zenyt spécifiquement pour ce client, ou comme quelque chose à apporter à votre portefeuille ?",
            ""
        ])
    
    lines.extend([
        f"Nous lançons l'analyse IA sur {website_domain} aujourd'hui pour avoir des résultats en direct.",
        "",
        f"Les marques e-commerce ont des défis QA uniques : {industry_info['challenges']} qui impactent la conversion et la confiance des clients.",
        "",
        "Quelques questions pour m'aider à adapter l'analyse :",
        "• Sur quels métriques de conversion vous concentrez-vous actuellement ?",
        "• Rencontrez-vous des défis spécifiques avec la précision des informations produit ou la cohérence ?",
    ])
    
    if is_agency:
        lines.append("• Est-ce quelque chose que vous apporteriez à d'autres clients également ?")
    else:
        lines.append("• Assurer la précision des spécifications produit est-il une priorité ?")
    
    lines.extend([
        "",
        "Seriez-vous disponible pour un appel de 30 minutes pour passer en revue les résultats ?",
        "",
        "Voici mon calendrier: https://calendly.com/arthur-pentecoste/zenyt-book-a-demo",
        "",
        "Cordialement,",
        "Arthur",
        "",
        f"P.S. {industry_info['ps_line']}"
    ])
    
    return "\n".join(lines)
