"""V6.6 paper-only exploratory risk: never confuse missing evidence with verified safety.

No network requests or real accounts used. All quotes and risk summaries mocked.
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.security import SecurityGate, SecurityVerdict
from hunter4.__main__ import PaperHunterV4

M='7'*44
from hunter2.bonding_quote import canonical_curve_address
C=canonical_curve_address(M)
A='A'*44
B='B'*44
GOOD={'score_normalised':10,'risks':[]}

def response(allowed=True,reasons=()):
    return SimpleNamespace(allowed=allowed,reasons=reasons)

class ExploratoryRugTests(unittest.TestCase):
    def gate(self,ans,**kw):
        gate=SecurityGate('https://api.mainnet-beta.solana.com',20,True,
                          paper_exploratory=True,paper_rug_score_ceiling=45,**kw)
        gate.rpc.examine=lambda *_:ans
        return gate

    def test_rpc_429_with_independent_clean_report_is_provisional(self):
        gate=self.gate(response(False,['risk data unavailable: RemoteError: POST HTTP 429']))
        with patch('hunter2.security.get_json',return_value=GOOD):
            result=gate.examine(M,C)
        self.assertTrue(result.approved)
        self.assertTrue(result.provisional)
        self.assertEqual(result.score,10)
        self.assertIn('RPC throttled',result.notes[0])

    def test_rpc_429_and_rugcheck_outage_are_blocked(self):
        gate=self.gate(response(False,['risk data unavailable: RemoteError: POST HTTP 429']))
        with patch('hunter2.security.get_json',side_effect=TimeoutError):
            r=gate.examine(M,C)
        self.assertFalse(r.approved)
        self.assertTrue(any('both rug evidence' in note for note in r.reasons))

    def test_critical_rugcheck_blocks_even_on_rpc_outage(self):
        gate=self.gate(response(False,['risk data unavailable: RemoteError: POST HTTP 429']))
        with patch('hunter2.security.get_json',return_value={
            'score_normalised':1,'risks':[{'level':'critical','name':'LP drain'}]}):
            r=gate.examine(M,C)
        self.assertFalse(r.approved)
        self.assertIn('rugcheck: LP drain',r.reasons)

    def test_verified_mint_authority_still_rejected(self):
        gate=self.gate(response(False,['mint authority active']))
        with patch('hunter2.security.get_json',return_value=GOOD):
            r=gate.examine(M,C)
        self.assertFalse(r.approved)
        self.assertTrue(any('mint authority' in why for why in r.reasons))

    def test_verified_concentration_remains_rejected(self):
        gate=self.gate(response(False,['large non-curve token accounts: 89.0% of supply']))
        with patch('hunter2.security.get_json',return_value=GOOD):
            self.assertFalse(gate.examine(M,C).approved)

    def test_high_rug_score_still_blocked(self):
        gate=self.gate(response(True))
        with patch('hunter2.security.get_json',return_value={'score_normalised':65,'risks':[]}):
            r=gate.examine(M,C)
        self.assertFalse(r.approved)
        self.assertTrue(any('65' in why for why in r.reasons))

    def test_medium_score_is_provisional_not_certified(self):
        gate=self.gate(response(True))
        with patch('hunter2.security.get_json',return_value={'score_normalised':40,'risks':[]}):
            r=gate.examine(M,C)
        self.assertTrue(r.approved)
        self.assertTrue(r.provisional)
        self.assertEqual(r.score,40)

    def test_missing_rugcheck_with_clean_onchain_is_provisional(self):
        gate=self.gate(response(True))
        with patch('hunter2.security.get_json',side_effect=TimeoutError):
            r=gate.examine(M,C)
        self.assertTrue(r.approved)
        self.assertTrue(r.provisional)
        self.assertIsNone(r.score)

    def test_bogus_curve_address_still_rejected(self):
        gate=self.gate(response(True))
        with patch('hunter2.security.get_json',return_value=GOOD):
            self.assertFalse(gate.examine(M,None).approved)

    def test_unknown_non429_rpc_failure_remains_rejected(self):
        gate=self.gate(response(False,['risk data unavailable: malformed response']))
        with patch('hunter2.security.get_json',return_value=GOOD):
            self.assertFalse(gate.examine(M,C).approved)

    def test_strict_mode_remains_fail_closed(self):
        gate=SecurityGate('https://x',20,True,paper_exploratory=False)
        gate.rpc.examine=lambda *_:response(False,['risk data unavailable: RemoteError: POST HTTP 429'])
        with patch('hunter2.security.get_json',return_value=GOOD):
            self.assertFalse(gate.examine(M,C).approved)
        gate.rpc.examine=lambda *_:response(True)
        with patch('hunter2.security.get_json',return_value={'score_normalised':40,'risks':[]}):
            self.assertFalse(gate.examine(M,C).approved)

    def test_provisional_paper_entry_is_minimum_two_dollars(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        cfg=Settings(db_path=str(Path(tmp.name)/'ledger.sqlite'),
                     min_liquidity_usd=3000, max_open_positions=200,assumed_slippage_pct=.0035)
        bot=PaperHunterV4(cfg);self.addCleanup(bot.db.close)
        bot.db.remember_mint(M,C)
        bot.consensus.record(M,A,'buy');bot.consensus.record(M,B,'buy')
        bot.security.examine=lambda *_:SecurityVerdict(True,(),10,True,('onchain throttled',))
        quote=Quote(M,.001,3000,30000,'verified',time.time())
        async def go():
            with patch('hunter2.engine.fetch_quote',return_value=quote):
                await bot.tick()
        asyncio.run(go())
        p=bot.broker.state()['positions'][M]
        self.assertAlmostEqual(p['basis'],2,places=5)
        self.assertEqual(p['signal_strength'],0)
        self.assertEqual(len(bot.db.recent_fills()),1)
        kinds=[k for (k,) in bot.db.conn.execute('select kind from observations')]
        self.assertIn('paper_rug_uncertain',kinds)
        self.assertIn('paper_buy',kinds)

    def test_provisional_refused_without_adaptive_sizing(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        cfg=Settings(db_path=str(Path(tmp.name)/'ledger.sqlite'),
                     adaptive_paper_sizing=False,max_open_positions=200,min_liquidity_usd=3000)
        bot=PaperHunterV4(cfg);self.addCleanup(bot.db.close)
        bot.db.remember_mint(M,C)
        bot.consensus.record(M,A,'buy');bot.consensus.record(M,B,'buy')
        bot.security.examine=lambda *_:SecurityVerdict(True,(),10,True,('audit missing',))
        async def go():
            with patch('hunter2.engine.fetch_quote',return_value=Quote(M,.001,3000,30000,'verified',time.time())):
                await bot.tick()
        asyncio.run(go())
        self.assertEqual(bot.db.recent_fills(),[])

    def test_exploratory_config_is_validated(self):
        cfg=Settings()
        self.assertTrue(cfg.paper_exploratory_rug)
        cfg.validate()
        cfg.paper_rug_score_ceiling=90
        self.assertRaises(ValueError,cfg.validate)
        cfg.paper_rug_score_ceiling=45
        cfg.paper_exploratory_rug='yes'
        self.assertRaises(ValueError,cfg.validate)

if __name__=='__main__':unittest.main()
