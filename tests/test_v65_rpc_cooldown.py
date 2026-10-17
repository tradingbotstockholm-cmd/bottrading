"""Offline regression tests for public RPC HTTP-429 discipline."""
import io
import unittest
import urllib.error
from unittest.mock import patch

from hunter.http import RemoteError, _retry_after_seconds, post_json
from hunter2.chain import SolanaClient, RpcRateLimited


class RpcCooldownTests(unittest.TestCase):
    def new_client(self):
        return SolanaClient('https://api.mainnet-beta.solana.com')

    def test_http_429_exposes_status_and_retry_after(self):
        error = urllib.error.HTTPError('https://example.org/rpc',429,'Too many requests',
                                       {'Retry-After':'45'},io.BytesIO(b'{"error":{"code":429}}'))
        with patch('hunter.http.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(RemoteError) as caught:
                post_json('https://example.org/rpc',{'method':'getTransaction'})
        self.assertEqual(caught.exception.status,429)
        self.assertEqual(caught.exception.retry_after,45)
        self.assertIn('HTTP 429',str(caught.exception))
        self.assertNotIn('https://example.org/rpc',str(caught.exception))

    def test_429_immediately_opens_method_cooldown_without_retries(self):
        client=self.new_client()
        with patch('hunter2.chain.time.sleep'),patch('hunter2.chain.post_json',side_effect=RemoteError('HTTP 429',status=429,retry_after=50)) as rpc:
            with self.assertRaises(RpcRateLimited):client.transaction('sig')
            self.assertEqual(rpc.call_count,1)
            with self.assertRaises(RpcRateLimited):client.transaction('sig')
            self.assertEqual(rpc.call_count,1,'cooldown must suppress network requests')
            self.assertGreater(client._cooldown_until['getTransaction'],0)

    def test_rate_limited_method_does_not_block_signature_discovery(self):
        client=self.new_client()
        with patch('hunter2.chain.time.sleep'),patch('hunter2.chain.post_json',side_effect=[
            RemoteError('HTTP 429',status=429), {'result': []}]) as rpc:
            with self.assertRaises(RpcRateLimited):client.transaction('sig')
            self.assertEqual(client.signatures('wallet'),[])
            self.assertEqual(rpc.call_count,2)

    def test_recovers_after_cooldown_and_success_resets_strikes(self):
        client=self.new_client()
        with patch('hunter2.chain.time.sleep'),patch('hunter2.chain.post_json',side_effect=[
            RemoteError('HTTP 429',status=429,retry_after=0), {'result': {'slot': 10}}]) as rpc:
            with self.assertRaises(RpcRateLimited):client.transaction('sig')
            client._cooldown_until['getTransaction']=0
            self.assertEqual(client.transaction('sig'),{'slot':10})
            self.assertEqual(client._rate_limit_strikes['getTransaction'],0)
            self.assertEqual(rpc.call_count,2)

    def test_429_from_json_error_also_cools_down(self):
        client=self.new_client()
        with patch('hunter2.chain.time.sleep'),patch('hunter2.chain.post_json',return_value={'error':{'code':429,'message':'rate limited'}}) as rpc:
            with self.assertRaises(RpcRateLimited):client.transaction('sig')
            self.assertEqual(rpc.call_count,1)

    def test_retry_after_bound_and_invalid(self):
        self.assertEqual(_retry_after_seconds('2'),2)
        self.assertEqual(_retry_after_seconds('999999'),999999)
        self.assertEqual(_retry_after_seconds('-3'),0)
        self.assertIsNone(_retry_after_seconds('not-a-time'))
        self.assertIsNone(_retry_after_seconds(None))

    def test_non429_errors_preserve_existing_bounded_retries(self):
        client=self.new_client()
        with patch('hunter2.chain.time.sleep'),patch('hunter2.chain.post_json',side_effect=RemoteError('server unavailable',status=503)) as rpc:
            with self.assertRaises(RemoteError) as caught:client.signatures('wallet')
            self.assertNotIsInstance(caught.exception,RpcRateLimited)
            self.assertEqual(rpc.call_count,3)

    def test_public_transaction_is_paced_but_custom_endpoint_is_not(self):
        pub=self.new_client();private=SolanaClient('https://example.org/api')
        for client in (pub,private):
            client._last_call=0
            client._last_transaction=__import__('time').monotonic()
        with patch('hunter2.chain.time.sleep') as sleep, patch('hunter2.chain.post_json',return_value={'result':{}}):
            pub.transaction('a')
            self.assertTrue(any(args.args[0]>2 for args in sleep.call_args_list))
        with patch('hunter2.chain.time.sleep') as sleep,patch('hunter2.chain.post_json',return_value={'result':{}}):
            private.transaction('a')
            self.assertFalse(any(args.args[0]>2 for args in sleep.call_args_list))

if __name__=='__main__':unittest.main()
