from django.core.management.base import BaseCommand
from knowledge.models import KnowledgeBase


SEED_DATA = [
    {
        "title": "About BidAlert",
        "content": """BidAlert.in is a B2B lead intelligence and tender alert platform built for Indian businesses.
We help companies discover government tenders, private procurement opportunities, and business leads — 
all in one place. Founded in 2023, we serve 500+ businesses across India.
Our mission: help SMEs compete for government and enterprise contracts without missing a single opportunity.""",
    },
    {
        "title": "Core Product — Tender Alert Service",
        "content": """BidAlert's flagship product is real-time tender alerts.
Features:
- Alerts from 10,000+ government portals (GeM, CPPP, state portals, railways, defence)
- Email + WhatsApp notifications within 2 hours of tender publication
- Smart keyword matching — set your categories once, we alert on relevant tenders only
- Tender documents download (no portal login needed)
- Bid tracking: upcoming deadlines, corrigenda, results
- Coverage: central government, all state governments, PSUs, municipal corporations""",
    },
    {
        "title": "Pricing Plans",
        "content": """BidAlert Pricing (as of 2024):

STARTER — ₹999/month
- Up to 50 tender alerts/month
- 1 keyword category
- Email alerts only
- 1 user seat

GROWTH — ₹2,499/month
- Up to 500 tender alerts/month
- 5 keyword categories
- Email + WhatsApp alerts
- 3 user seats
- Document downloads included

PRO — ₹5,999/month
- Unlimited tender alerts
- 20 keyword categories
- Email + WhatsApp + SMS alerts
- 10 user seats
- Priority document downloads
- Dedicated account manager

ENTERPRISE — Custom pricing
- Custom keyword categories
- API access
- White-label options
- Dedicated support
- SLA guarantee

All plans include: 14-day free trial, cancel anytime, GST invoice provided.""",
    },
    {
        "title": "Lead Intelligence Feature",
        "content": """BidAlert Lead Intelligence (available on Growth+ plans):
- Discover companies actively bidding in your sector
- Get contact info for procurement heads and decision-makers  
- Filter by state, industry, company size, and tender history
- Export leads to Excel/CSV
- Integration with popular CRMs (Zoho, Salesforce) via webhook
Use case: If you supply IT hardware, find all companies that bid for IT tenders 
and build a verified prospect list automatically.""",
    },
    {
        "title": "Support and Onboarding",
        "content": """BidAlert Support:
- Email: support@bidalert.in (response within 4 business hours)
- WhatsApp support: +91-XXXXXXXXXX (Growth+ plans)
- Onboarding call: Free 30-min setup call for all new subscribers
- Help centre: help.bidalert.in
- Video tutorials: Available for all features

Onboarding process:
1. Sign up and choose a plan
2. Schedule your free onboarding call
3. Set up your keyword categories with our team
4. Receive your first alerts within 24 hours""",
    },
    {
        "title": "Success Stories and ROI",
        "content": """BidAlert customer outcomes (anonymised):
- Construction company (Pune): Won ₹2.3 Cr government contract within 3 months of subscribing
- IT services firm (Bengaluru): Discovered 47 new tenders in their niche in first month
- Medical equipment supplier: Reduced tender research time from 4 hours/day to 20 minutes
- Average customer ROI: 8x within 6 months

Key benefit: Never miss a tender again. Our customers report discovering 3x more 
relevant opportunities compared to manual portal browsing.""",
    },
    {
        "title": "Free Trial Details",
        "content": """BidAlert Free Trial:
- 14 days, no credit card required
- Full access to GROWTH plan features during trial
- Up to 200 tender alerts during trial period
- Onboarding call included
- At trial end: automatically move to free tier OR choose a paid plan
- No auto-charge without explicit confirmation

To start trial: Visit bidalert.in, click 'Start Free Trial', enter your business email.""",
    },
]


class Command(BaseCommand):
    help = 'Seed KnowledgeBase with BidAlert product facts'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for item in SEED_DATA:
            obj, was_created = KnowledgeBase.objects.get_or_create(
                title=item['title'],
                defaults={'content': item['content'], 'is_active': True}
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Knowledge Base seeded: {created} created, {skipped} already existed.'
            )
        )
