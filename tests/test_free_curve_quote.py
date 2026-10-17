"""Offline, read-only Pump.fun quote fallback tests, no external API needed."""
import asyncio,base64,struct,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from hunter2 import bonding_quote as bc
from hunter2.engine import Settings
from hunter4.__main__ import PaperHunterV4
from hunter.models import Quote

MINT='6NLUC3veg7NGuQBNewE1UM7G6ir2mymJM4PvwdkGpump'
SUPPLY=1_000_000_000*10**6


def curve_bytes(*,complete=False,virtual_sol=45_000_000_000,real_sol=14_000_000_000):
    return bc.DISCRIMINATOR + struct.pack('<5Q',800_000_000*10**6,virtual_sol,500_000_000*10**6,real_sol,SUPPLY)+bytes([int(complete)])+b'\x00'*32


def mint_bytes(*, supply=SUPPLY, decimals=6):
    out=bytearray(82)
    out[36:44]=struct.pack('<Q',supply)
    out[44]=decimals
    out[45]=1
    return bytes(out)


def account(body,owner):return {'owner':owner,'data':[base64.b64encode(body).decode(),'base64']}


def rpc_payload(*,complete=False,mint_program=bc.SPL_TOKEN_PROGRAM,decimals=6,supply=SUPPLY,quote_mint=None):
    return {'result':{'context':{'slot':50},'value':[
        account(curve_bytes(complete=complete)+(b'\x00'*2)+(quote_mint if quote_mint is not None else b'\x00'*32),bc.PUMP_PROGRAM),
        account(mint_bytes(supply=supply,decimals=decimals),mint_program)]}}

class PumpCurveQuote(unittest.TestCase):
    def test_base58_roundtrip(self):
        for x in (bytes(32),bytes(range(32)),bytes([255]*32)):
            self.assertEqual(bc._b58decode(bc._b58encode(x)),x)

    def test_pda_is_deterministic_and_off_curve(self):
        pda=bc.canonical_curve_address(MINT)
        self.assertEqual(pda,bc.canonical_curve_address(MINT))
        self.assertEqual(len(bc._b58decode(pda)),32)
        self.assertTrue(not bc._ed25519_point(bc._b58decode(pda)))

    def test_curve_prices_use_real_not_virtual_liquidity(self):
        r=bc.read_reserves(curve_bytes())
        quote=bc.price_from_reserves(MINT,r,200.0,now=123)
        self.assertAlmostEqual(quote.price_usd,0.00001125)
        self.assertAlmostEqual(quote.market_cap_usd,11250)
        self.assertAlmostEqual(quote.liquidity_usd,2800)
        self.assertEqual(quote.dex_id,'pump_curve_onchain')
        self.assertEqual(quote.timestamp,123)

    def test_completed_or_corrupt_curve_rejected(self):
        self.assertIsNone(bc.read_reserves(curve_bytes(complete=True)))
        self.assertIsNone(bc.read_reserves(curve_bytes()[:35]))
        self.assertIsNone(bc.read_reserves(b'X'*8+curve_bytes()[8:]))

    def test_verified_rpc_quote_with_matching_accounts(self):
        curve=bc.canonical_curve_address(MINT)
        with patch.object(bc,'post_json',return_value=rpc_payload()) as rpc, patch.object(bc,'fresh_sol_usd',return_value=220.0):
            quote=bc.fetch_curve_quote(MINT,curve,'https://rpc.example')
        self.assertIsNotNone(quote)
        self.assertGreater(quote.market_cap_usd,10000)
        self.assertAlmostEqual(quote.liquidity_usd,3080)
        self.assertEqual(rpc.call_args.args[1]['method'],'getMultipleAccounts')

    def test_new_token2022_sol_quote_accepted(self):
        curve=bc.canonical_curve_address(MINT)
        x=rpc_payload(mint_program=bc.TOKEN_2022_PROGRAM)
        with patch.object(bc,'post_json',return_value=x),patch.object(bc,'fresh_sol_usd',return_value=220.0):
            self.assertIsNotNone(bc.fetch_curve_quote(MINT,curve,'https://rpc.example'))

    def test_rejects_non_sol_quote_for_both_mint_types(self):
        curve=bc.canonical_curve_address(MINT)
        quote_mint=bytes(range(32))
        for program in (bc.SPL_TOKEN_PROGRAM,bc.TOKEN_2022_PROGRAM):
            with self.subTest(program=program),patch.object(bc,'post_json',return_value=rpc_payload(mint_program=program,quote_mint=quote_mint)):
                self.assertIsNone(bc.fetch_curve_quote(MINT,curve,'https://rpc.example'))

    def test_rejects_token2022_without_quote_denomination(self):
        curve=bc.canonical_curve_address(MINT)
        x=rpc_payload(mint_program=bc.TOKEN_2022_PROGRAM)
        x['result']['value'][0]=account(curve_bytes(),bc.PUMP_PROGRAM)
        with patch.object(bc,'post_json',return_value=x):
            self.assertIsNone(bc.fetch_curve_quote(MINT,curve,'https://rpc.example'))

    def test_rejects_curve_not_derived_from_token(self):
        with patch.object(bc,'post_json',side_effect=AssertionError('no RPC for mismatched PDA')):
            self.assertIsNone(bc.fetch_curve_quote(MINT,bc.PUMP_PROGRAM,'https://rpc.example'))

    def test_rejects_mint_quote_ambiguity_and_bad_supply(self):
        curve=bc.canonical_curve_address(MINT)
        for x in (rpc_payload(mint_program=bc.PUMP_PROGRAM),rpc_payload(decimals=9),rpc_payload(supply=SUPPLY-1),rpc_payload(complete=True)):
            with self.subTest(x=x['result']['value'][1]['owner']),patch.object(bc,'post_json',return_value=x),patch.object(bc,'fresh_sol_usd',return_value=200):
                self.assertIsNone(bc.fetch_curve_quote(MINT,curve,'https://rpc.example'))

    def test_rejects_forged_program_owner_and_invalid_sol_quote(self):
        x=rpc_payload()
        x['result']['value'][0]['owner']=bc.SPL_TOKEN_PROGRAM
        curve=bc.canonical_curve_address(MINT)
        with patch.object(bc,'post_json',return_value=x):
            self.assertIsNone(bc.fetch_curve_quote(MINT,curve,'https://rpc.example'))
        self.assertIsNone(bc.price_from_reserves(MINT,bc.read_reserves(curve_bytes()),float('nan')))

    def test_no_oracle_does_not_make_up_price(self):
        curve=bc.canonical_curve_address(MINT)
        with patch.object(bc,'post_json',return_value=rpc_payload()),patch.object(bc,'fresh_sol_usd',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):bc.fetch_curve_quote(MINT,curve,'https://rpc.example')

    def test_full_paper_buy_and_sell_on_verified_curve_quotes(self):
        from hunter2.security import SecurityVerdict
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        cfg=Settings(db_path=str(Path(tmp.name)/'real_observation_model.sqlite'),
                     max_open_positions=200,min_liquidity_usd=3000,assumed_slippage_pct=.0035)
        bot=PaperHunterV4(cfg);self.addCleanup(bot.db.close)
        curve=bc.canonical_curve_address(MINT)
        bot.db.remember_mint(MINT,curve)
        bot.security.examine=lambda *_:SecurityVerdict(True,(),10)
        bot.consensus.record(MINT,'A','buy')
        bot.consensus.record(MINT,'B','buy')
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=None),\
                 patch.object(bc,'post_json',return_value=rpc_payload()),\
                 patch.object(bc,'fresh_sol_usd',return_value=220.0):
                await bot.tick()
            self.assertEqual([x['side'] for x in bot.db.recent_fills()],['buy'])
            self.assertIn(MINT,bot.db.state()['positions'])
            # Simulate an actual changed, on-chain reserve snapshot leading to
            # a downside exit; never synthesize a sale unless risk engine does.
            falling=rpc_payload()
            falling['result']['value'][0]=account(curve_bytes(virtual_sol=30_000_000_000),bc.PUMP_PROGRAM)
            with patch('hunter2.engine.fetch_quote',return_value=None),\
                 patch.object(bc,'post_json',return_value=falling),\
                 patch.object(bc,'fresh_sol_usd',return_value=220.0):
                await bot.tick()
            self.assertEqual([x['side'] for x in reversed(bot.db.recent_fills())],['buy','sell'])
            self.assertNotIn(MINT,bot.db.state()['positions'])
            self.assertLess(bot.db.recent_fills()[0]['pnl_usd'],0)
        asyncio.run(scenario())

    def test_engine_uses_verified_curve_fallback_without_disabling_risk(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        settings=Settings(db_path=str(Path(tmp.name)/'paper.sqlite'),min_liquidity_usd=3000,assumed_slippage_pct=.0035)
        bot=PaperHunterV4(settings);self.addCleanup(bot.db.close)
        bot.db.remember_mint(MINT,bc.canonical_curve_address(MINT))
        quote=Quote(MINT,.000014,15000,4500,'pump_curve_onchain',time.time())
        with patch('hunter2.engine.fetch_quote',return_value=None),patch('hunter2.engine.fetch_curve_quote',return_value=quote):
            self.assertEqual(bot.market_quote(MINT),quote)
        # risk remains independent and must still decline disallowed tokens
        from hunter2.security import SecurityVerdict
        bot.security.examine=lambda *_:SecurityVerdict(False,('risk blocked',),60)
        bot.consensus.record(MINT,'A','buy')
        bot.consensus.record(MINT,'B','buy')
        async def enter():
            with patch('hunter2.engine.fetch_quote',return_value=None),patch('hunter2.engine.fetch_curve_quote',return_value=quote):
                await bot.try_entry(MINT)
        asyncio.run(enter())
        self.assertEqual(bot.db.recent_fills(),[])
        self.assertEqual(bot.db.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='rug_reject'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
