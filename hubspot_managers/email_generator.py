"""
Email Generator for Demo Request Emails
Generates demo request pre-call emails matching zenyt-docs/sales/inbound/pre-call-email-template.md
"""
import logging
import re
from typing import Dict, Optional
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

CALENDLY_30 = "https://calendly.com/arthur-pentecoste/zenyt-book-a-demo"


def extract_domain(url_or_email: str) -> str:
    """Extract domain from URL or email"""
    if not url_or_email:
        return ""
    domain = re.sub(r'^https?://', '', url_or_email.lower())
    domain = re.sub(r'^www\.', '', domain)
    domain = domain.split('/')[0].split('?')[0].split('@')[-1]
    return domain.strip()


def detect_agency(contact_email: str, website_url: str) -> tuple:
    """
    Detect if contact is from an agency.
    Returns: (is_agency, agency_name, email_domain)
    """
    if not contact_email or not website_url:
        return False, None, None

    email_domain = extract_domain(contact_email)
    website_domain = extract_domain(website_url)

    if email_domain != website_domain:
        agency_name = email_domain.split('.')[0].capitalize()
        return True, agency_name, email_domain

    return False, None, email_domain


def detect_language(website_url: str, company_country: Optional[str] = None) -> str:
    if not website_url:
        return "en"
    domain = extract_domain(website_url)
    tld = domain.split('.')[-1] if '.' in domain else ""
    if tld in ['fr', 'be', 'ch', 'lu', 'mc']:
        return "fr"
    if company_country:
        country_lower = company_country.lower()
        if any(c in country_lower for c in ['france', 'french', 'belgium', 'switzerland']):
            return "fr"
    return "en"


INDUSTRY_MAP = {
    'fashion': {
        'keywords': ['fashion', 'clothing', 'apparel', 'retail', 'streetwear', 'activewear', 'denim'],
        'context': "Fashion brands have unique QA challenges: sizing consistency across collections, variant accuracy (colors, materials), and product content alignment all impact conversion and reduce returns.",
        'ps': "Many fashion brands tell us that variant accuracy is critical for reducing returns and maintaining conversion.",
        'questions': [
            "What conversion metrics are you focused on improving right now?",
            "Are you seeing specific challenges with variant accuracy or product content consistency?",
        ],
    },
    'luxury_fashion': {
        'keywords': ['luxury', 'couture', 'designer', 'haute', 'bridal', 'gown'],
        'context': "Luxury fashion brands have unique QA challenges: sizing accuracy, materials consistency, care instructions, and imagery alignment. These details drive conversion and reduce returns on high-value purchases.",
        'ps': "We work with fashion brands like NY&Company and luxury companies like London Victorian Ring on sizing and materials accuracy. For occasion wear where returns are costly, these details matter even more.",
        'questions': [
            "Which product lines are highest priority?",
            "Are you seeing specific challenges with product content quality or consistency?",
        ],
    },
    'beauty': {
        'keywords': ['beauty', 'skincare', 'cosmetics', 'makeup', 'hair', 'fragrance', 'perfume'],
        'context': "Beauty and skincare brands have unique QA challenges: ingredient accuracy, claims consistency, and product information alignment all impact conversion and customer confidence.",
        'ps': "Many beauty brands tell us that ingredient accuracy and claims consistency are critical for compliance and conversion.",
        'questions': [
            "Are you seeing specific challenges with ingredient accuracy or claims consistency?",
            "Is compliance a concern?",
        ],
    },
    'jewelry': {
        'keywords': ['jewelry', 'jewellery', 'watches', 'watch', 'ring', 'diamond', 'gold'],
        'context': "For luxury jewelry and watch retailers, the AI focuses on spec accuracy (case sizes, materials, reference numbers), certification details, and consistent product presentation. Critical for high-AOV purchases where buyers need complete confidence.",
        'ps': "We work with many jewelry and luxury companies, like London Victorian Ring. Happy to share patterns we've seen in high-AOV catalogs where product accuracy directly impacts buyer confidence.",
        'questions': [
            "Which categories are you driving the most traffic to right now?",
            "Any specific areas where you're seeing drop-off in conversion?",
        ],
    },
    'outdoor_sports': {
        'keywords': ['outdoor', 'sport', 'ski', 'snowboard', 'cycling', 'bike', 'running', 'hiking', 'athletic'],
        'context': "Technical sports brands have unique QA challenges: specs accuracy (waterproof ratings, materials), sizing for performance fit, and activity-specific categorization all impact conversion on high-ticket purchases.",
        'ps': "Premium outdoor brands often see the biggest impact from technical spec accuracy. Customers research heavily before $500+ purchases.",
        'questions': [
            "What conversion metrics are you focused on improving right now?",
            "Are you seeing specific challenges with product specification accuracy?",
        ],
    },
    'electronics': {
        'keywords': ['electronics', 'technology', 'tech', 'networking', 'hardware', 'software', 'computer'],
        'context': "For electronics and technology products, spec accuracy and compatibility information are critical for buyer confidence. Technical buyers compare specs across sites before purchasing.",
        'ps': "Technical buyers research specs extensively before purchasing. Accuracy directly impacts conversion on high-consideration products.",
        'questions': [
            "Are there specific challenges with spec accuracy or compatibility information?",
            "What's your current process for maintaining technical specifications?",
        ],
    },
    'home_goods': {
        'keywords': ['home', 'furniture', 'appliance', 'kitchen', 'decor', 'interior', 'garden'],
        'context': "Home goods brands need spec accuracy on complex products. Buyers compare dimensions, materials, and features across sites before committing to high-value purchases.",
        'ps': "Many luxury home goods brands tell us that spec accuracy on complex products is critical for both conversion and reducing returns.",
        'questions': [
            "What conversion metrics are you focused on improving right now?",
            "Are you seeing specific challenges with product specs accuracy or content quality?",
        ],
    },
    'supplement': {
        'keywords': ['supplement', 'vitamin', 'nutrition', 'wellness', 'health', 'nutraceutical', 'protein'],
        'context': "Supplement and wellness brands have unique QA challenges: ingredient accuracy, claims consistency, and product information alignment all impact conversion and customer confidence.",
        'ps': "Many supplement brands tell us that ingredient accuracy and claims consistency are critical for customer confidence and compliance.",
        'questions': [
            "Are you seeing specific challenges with ingredient accuracy or claims consistency?",
            "Is compliance a concern?",
        ],
    },
    'swimwear': {
        'keywords': ['swim', 'swimwear', 'bikini', 'beachwear'],
        'context': "For swimwear brands, sizing accuracy, material details, and variant consistency become critical conversion drivers. Small inconsistencies create friction, especially around fit and fabric claims.",
        'ps': "Most swimwear brands tell us sizing and material accuracy are high-leverage conversion and returns drivers.",
        'questions': [
            "What made you reach out now?",
            "Are you seeing specific challenges around sizing or product content?",
        ],
    },
    'b2b': {
        'keywords': ['b2b', 'industrial', 'equipment', 'manufacturing', 'wholesale', 'supply', 'medical', 'ppe', 'safety'],
        'context': "With B2B products, spec accuracy is critical for compliance and buyer confidence. Buyers need precise technical details and certification information before purchasing.",
        'ps': "B2B buyers need precise certification and spec details before purchasing. Accuracy directly impacts order rates.",
        'questions': [
            "Which product categories are priority?",
            "What's your current process for maintaining spec accuracy across your catalog?",
        ],
    },
    'multi_brand': {
        'keywords': ['multi-brand', 'multi brand', 'department', 'marketplace', 'retailer'],
        'context': "Multi-brand retailers have unique QA challenges: sizing consistency across brands, variant accuracy (colors, materials), and product content alignment all impact conversion and reduce returns, especially when managing dozens of premium labels.",
        'ps': "Multi-brand operators see the biggest impact from cross-brand consistency. When every label has different content standards, systematic detection prevents errors from compounding across the catalog.",
        'questions': [
            "Which areas are you seeing the most friction right now (product pages, categorization, content consistency)?",
            "Is content consistency across brands a challenge?",
        ],
    },
    'food_beverage': {
        'keywords': ['food', 'beverage', 'drink', 'wine', 'beer', 'coffee', 'tea', 'grocery', 'gourmet'],
        'context': "Food and beverage brands need ingredient accuracy, allergen information consistency, and nutritional claims alignment. These details impact trust and compliance.",
        'ps': "Food and beverage buyers expect ingredient accuracy and allergen consistency. These details impact trust and regulatory compliance.",
        'questions': [
            "Are you seeing specific challenges with ingredient or nutritional information accuracy?",
            "Is allergen or regulatory compliance a priority?",
        ],
    },
    'eyewear': {
        'keywords': ['eyewear', 'optical', 'glasses', 'sunglasses', 'lens'],
        'context': "Eyewear brands need frame specification accuracy (dimensions, lens options, prescription compatibility), sizing consistency, and prescription information alignment.",
        'ps': "Many eyewear brands tell us that accurate frame specifications and prescription compatibility information are critical for customer trust and reducing returns on high-value purchases.",
        'questions': [
            "Which product categories are priority (prescription, sunglasses, both)?",
            "Are you seeing specific challenges with frame specifications or compatibility?",
        ],
    },
}

DEFAULT_INDUSTRY = {
    'context': "E-commerce brands have unique QA challenges: product information accuracy, specifications consistency, and categorization all impact conversion and customer trust.",
    'ps': "Many e-commerce brands tell us that product information accuracy is critical for customer trust and conversion.",
    'questions': [
        "What conversion metrics are you focused on improving right now?",
        "Are you seeing specific challenges with product information accuracy or consistency?",
    ],
}


def get_industry_info(industry: Optional[str], website_domain: str = "") -> Dict:
    """Match industry to the best template. Returns context, ps, and questions."""
    search_text = (industry or "").lower() + " " + (website_domain or "").lower()

    best_match = None
    best_score = 0
    for key, data in INDUSTRY_MAP.items():
        score = sum(1 for kw in data['keywords'] if kw in search_text)
        if score > best_score:
            best_score = score
            best_match = data

    if best_match:
        return best_match
    return DEFAULT_INDUSTRY


def generate_demo_request_email(
    contact_email: str,
    contact_firstname: str,
    contact_lastname: str,
    company_name: str,
    website_url: str,
    industry: Optional[str] = None,
    company_country: Optional[str] = None,
    scan_url: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Dict:
    """
    Generate demo request email content matching pre-call-email-template.md.

    scan_url: the URL submitted for analysis (may differ from company domain for agencies).
    linkedin_url: prospect's personal LinkedIn profile URL.
    """
    is_agency, agency_name, _ = detect_agency(contact_email, website_url)
    language = detect_language(website_url, company_country)

    scan_domain = extract_domain(scan_url or website_url)
    website_domain = extract_domain(website_url)
    industry_info = get_industry_info(industry, scan_domain)

    contact_name = f"{contact_firstname} {contact_lastname}".strip() or contact_email.split('@')[0]
    first_name = contact_firstname or contact_name.split()[0] if contact_name else "there"

    subject = f"launching analysis on {scan_domain}"

    if language == "fr":
        body = _build_french_email(
            first_name, scan_domain, is_agency, agency_name, industry_info, company_name
        )
    else:
        body = _build_english_email(
            first_name, scan_domain, is_agency, agency_name, industry_info, company_name
        )

    li_message = _build_linkedin_message(first_name, scan_domain)

    mailto_subject = quote(subject)
    mailto_body = quote(body)
    mailto_link = f"mailto:{contact_email}?subject={mailto_subject}&body={mailto_body}"

    return {
        "to": contact_email,
        "subject": subject,
        "body": body,
        "mailto_link": mailto_link,
        "linkedin_message": li_message,
        "linkedin_url": linkedin_url,
        "language": language,
        "is_agency": is_agency,
        "agency_name": agency_name,
        "industry": industry,
        "word_count": len(body.split()),
    }


def _build_english_email(
    first_name: str,
    scan_domain: str,
    is_agency: bool,
    agency_name: Optional[str],
    industry_info: Dict,
    company_name: str,
) -> str:
    lines = []
    lines.append(f"Hi {first_name},")
    lines.append("")
    lines.append(f"Thanks for your request to start with Zenyt.ai on {scan_domain}.")
    lines.append("")

    if is_agency and agency_name:
        lines.append(
            f"I see you're at {agency_name}. Given you're evaluating {scan_domain}, "
            "I'm curious: are you exploring Zenyt specifically for this client, or as "
            "something to bring across your portfolio?"
        )
        lines.append("")

    lines.append(f"We're launching the AI analysis on {scan_domain} today so I have live findings ready.")
    lines.append("")
    lines.append(industry_info['context'])
    lines.append("")

    lines.append("Quick questions so I can tailor findings to your situation:")
    for q in industry_info['questions']:
        lines.append(f"\u2022 {q}")
    if is_agency:
        lines.append("\u2022 Would this be something you'd bring to other clients as well?")
    lines.append("")

    lines.append("Would you be available for a 30 minute call to walk through findings?")
    lines.append("")
    lines.append(f"Here's my calendar: {CALENDLY_30}")
    lines.append("")
    lines.append("Best,")
    lines.append("Arthur")
    lines.append("")
    lines.append(f"P.S. {industry_info['ps']}")

    return "\n".join(lines)


def _build_french_email(
    first_name: str,
    scan_domain: str,
    is_agency: bool,
    agency_name: Optional[str],
    industry_info: Dict,
    company_name: str,
) -> str:
    lines = []
    lines.append(f"Bonjour {first_name},")
    lines.append("")
    lines.append(f"Merci pour votre demande de d\u00e9mo sur {scan_domain}.")
    lines.append("")

    if is_agency and agency_name:
        lines.append(
            f"Je vois que vous \u00eates chez {agency_name}. Vu que vous \u00e9valuez {scan_domain}, "
            "je suis curieux : explorez-vous Zenyt sp\u00e9cifiquement pour ce client, ou comme "
            "quelque chose \u00e0 apporter \u00e0 votre portefeuille ?"
        )
        lines.append("")

    lines.append(f"Nous lan\u00e7ons l'analyse IA sur {scan_domain} aujourd'hui pour avoir des r\u00e9sultats en direct.")
    lines.append("")
    lines.append(industry_info['context'])
    lines.append("")

    lines.append("Quelques questions pour mieux adapter nos agents IA \u00e0 vos besoins :")
    for q in industry_info['questions']:
        lines.append(f"\u2022 {q}")
    if is_agency:
        lines.append("\u2022 Est-ce quelque chose que vous apporteriez \u00e0 d'autres clients \u00e9galement ?")
    lines.append("")

    lines.append("Seriez-vous disponible pour un appel de 30 minutes pour passer en revue les r\u00e9sultats ?")
    lines.append("")
    lines.append(f"Voici mon calendrier: {CALENDLY_30}")
    lines.append("")
    lines.append("Cordialement,")
    lines.append("Arthur")
    lines.append("")
    lines.append(f"P.S. {industry_info['ps']}")

    return "\n".join(lines)


def _build_linkedin_message(first_name: str, scan_domain: str) -> str:
    return (
        f"Hi {first_name}.\n\n"
        f"I'm launching an AI analysis of {scan_domain} today. "
        "Would love to connect and share what we find.\n\n"
        "Arthur."
    )
