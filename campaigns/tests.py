import json
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from campaigns.models import Campaign
from campaigns.compose_views import _text_to_clean_html
from leads.models import Lead

User = get_user_model()


class DirectComposeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='opuser', password='password123', is_staff=True)
        self.client = Client()
        self.client.login(username='opuser', password='password123')

    def test_text_to_clean_html_conversion(self):
        sample_text = (
            "Respected Sir/Madam,\n\n"
            "Greetings from **V CHANGE SOLUTIONS**!\n\n"
            "1. Offer One\n"
            "2. Offer Two\n\n"
            "Website: https://vchangesolutions.com/"
        )
        html = _text_to_clean_html(sample_text)
        self.assertIn('Respected Sir/Madam,', html)
        self.assertIn('<strong>V CHANGE SOLUTIONS</strong>', html)
        self.assertIn('1. Offer One<br>\n2. Offer Two', html)
        self.assertIn('https://vchangesolutions.com/', html)
        self.assertIn('{{unsub_url}}', html)

    def test_compose_direct_get_view(self):
        resp = self.client.get('/compose/direct/')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        # Ensure HTML Editor tab and raw HTML textarea are removed
        self.assertNotIn('id="tabEditorHtml"', content)
        self.assertNotIn('name="body_html"', content)
        # Ensure Plain Text editor and Save buttons are present
        self.assertIn('name="body_text"', content)
        self.assertIn('id="btnSaveDraftHeader"', content)
        self.assertIn('id="btnSaveDraftBottom"', content)
        self.assertIn('Save Changes', content)

    def test_direct_save_draft_api(self):
        # Initial save creates a new draft campaign
        payload = {
            'name': 'Test Draft Campaign',
            'subject': 'Draft Proposal Subject',
            'body_text': 'Hello world,\n\nThis is a plain text draft.',
            'category': 'buyers',
            'lane': 'bulk',
        }
        resp = self.client.post(
            '/compose/direct/save-draft/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        camp_id = data['campaign_id']

        camp = Campaign.objects.get(id=camp_id)
        self.assertEqual(camp.status, Campaign.STATUS_DRAFT)
        self.assertEqual(camp.subject, 'Draft Proposal Subject')
        self.assertIn('Hello world,', camp.body_html)

        # Subsequent save updates the existing draft
        update_payload = {
            'campaign_id': camp_id,
            'name': 'Updated Draft Campaign',
            'subject': 'Updated Subject',
            'body_text': 'Hello world,\n\nUpdated plain text.',
            'category': 'buyers',
            'lane': 'bulk',
        }
        resp2 = self.client.post(
            '/compose/direct/save-draft/',
            data=json.dumps(update_payload),
            content_type='application/json'
        )
        self.assertEqual(resp2.status_code, 200)
        camp.refresh_from_db()
        self.assertEqual(camp.subject, 'Updated Subject')
        self.assertEqual(Campaign.objects.count(), 1)

    def test_compose_direct_save_as_draft_action_without_recipients(self):
        # Submitting send_action=draft with no recipients should succeed
        resp = self.client.post('/compose/direct/', {
            'name': 'Draft Without Recipients',
            'subject': 'Sample Subject',
            'body_text': 'Plain text draft content',
            'category': 'buyers',
            'lane': 'bulk',
            'send_action': 'draft',
            'raw_emails': '',
        })
        self.assertEqual(resp.status_code, 302)
        camp = Campaign.objects.filter(name='Draft Without Recipients').first()
        self.assertIsNotNone(camp)
        self.assertEqual(camp.status, Campaign.STATUS_DRAFT)
        self.assertEqual(camp.planned_total, 0)

