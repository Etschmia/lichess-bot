"""Isolated tests: no network, real challenges, or crontab writes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import requests
import variant_challenge_sweep as s


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.state = s.initialize([{'name': 'ExampleBot'}])
        self.row = self.state['attempts'][0]
        self.api = Mock()
        self.api.get.side_effect = lambda p: {'/api/account': {'id': 'martuni'}, '/api/account/playing': {'nowPlaying': []}, '/api/challenge': {'out': []}}[p]

    def pending(self, status, expired=False):
        self.row.update(status='pending', challenge_id='abcdefgh', deadline_epoch=0 if expired else s.time.time()+600)
        self.api.status.return_value = {'status': status}

    def test_matrix(self):
        bots = json.loads((s.ROOT/'variant_bots.json').read_text())
        rows = s.initialize(bots)['attempts']
        self.assertEqual(len(rows), 125)
        self.assertEqual(len({(r['opponent'],r['variant']) for r in rows}), 125)
        self.assertNotIn('Martuni', [r['opponent'] for r in rows])

    def test_accepted(self):
        self.pending('accepted')
        s.monitor(self.api,self.row,self.state,self.path)
        self.assertEqual(self.row['status'],'accepted')
        self.api.post.assert_not_called()

    def test_declined(self):
        self.pending('declined')
        s.monitor(self.api,self.row,self.state,self.path)
        self.assertEqual(self.row['status'],'declined')

    def test_timeout_cancel_and_verify(self):
        self.pending('created',True)
        self.api.status.side_effect=[{'status':'created'},{'status':'canceled'}]
        self.api.post.return_value.status_code=200
        s.monitor(self.api,self.row,self.state,self.path)
        self.assertEqual(self.row['status'],'timeout')
        self.assertEqual(self.api.status.call_count,2)
        self.api.post.assert_called_once_with('/api/challenge/abcdefgh/cancel')

    def test_cancel_failure_stays_pending(self):
        self.pending('created',True)
        self.api.post.side_effect=requests.ConnectionError()
        s.monitor(self.api,self.row,self.state,self.path)
        self.assertEqual(self.row['status'],'pending')

    def test_accepted_at_deadline_no_cancel(self):
        self.pending('accepted',True)
        s.monitor(self.api,self.row,self.state,self.path)
        self.api.post.assert_not_called()

    def test_capacity(self):
        with patch.object(s,'process_count',return_value=2):
            s.run(self.api,self.state,self.path)
        self.api.post.assert_not_called()
        self.assertEqual(self.row['status'],'queued')

    def test_payload_and_write_ahead(self):
        def post(path,data):
            saved=json.loads(self.path.read_text())
            self.assertEqual(saved['attempts'][0]['status'],'sending')
            self.assertEqual(data['rated'],'true')
            self.assertEqual(data['clock.limit'],'300')
            self.assertEqual(data['clock.increment'],'0')
            self.assertEqual(data['variant'],'antichess')
            return Mock(status_code=200,ok=True,json=lambda:{'challenge':{'id':'abcdefgh'}})
        self.api.post.side_effect=post
        self.api.status.return_value={'status':'declined','declineReasonKey':'casual'}
        with patch.object(s,'process_count',return_value=0), patch.object(s.subprocess,'run',return_value=Mock(returncode=0)):
            s.run(self.api,self.state,self.path)
        self.assertEqual(self.row['status'],'declined')
        self.assertEqual(self.api.post.call_count,1)

    def test_rate_limit(self):
        self.api.post.return_value=Mock(status_code=429,ok=False,json=lambda:{'ratelimit':{'seconds':300}})
        with patch.object(s,'process_count',return_value=0), patch.object(s.subprocess,'run',return_value=Mock(returncode=0)):
            s.run(self.api,self.state,self.path)
        self.assertEqual(self.row['status'],'queued')
        self.assertGreater(self.state['cooldown_until'],s.time.time())

    def test_cron_comment_only_own_job(self):
        original='0 1 * * * other\n*/20 * * * * runner '+s.MARKER+'\n# old '+s.MARKER+'\n'
        new=s.comment_cron(original)
        self.assertTrue(new.startswith('0 1 * * * other\n# completed */20'))
        self.assertEqual(s.comment_cron(new),new)

    def test_completion_disables(self):
        for r in self.state['attempts']:
            r['status']='declined'
        with patch.object(s,'disable_cron') as disable:
            s.run(self.api,self.state,self.path)
            disable.assert_called_once()
        self.api.post.assert_not_called()

    def test_crash_does_not_repeat(self):
        self.row.update(status='sending',deadline_epoch=0)
        s.run(self.api,self.state,self.path)
        self.assertEqual(self.row['status'],'send_unknown')
        self.api.post.assert_not_called()

    def test_pending_recovery_precedes_capacity(self):
        self.pending('accepted')
        with patch.object(s,'process_count',return_value=2):
            s.run(self.api,self.state,self.path)
        self.assertEqual(self.row['status'],'accepted')
        self.api.post.assert_not_called()


if __name__=='__main__':
    unittest.main()
