import json
import tempfile
import unittest
from pathlib import Path
from hunter.config import Config,load
from hunter.models import Quote
from hunter.dex import quote_from_pairs
from hunter.signals import Consensus
from hunter.fomo import parse_wallet_export
from hunter.paper import PaperBroker
from hunter.rug import SolanaRPC

MINT='So11111111111111111111111111111111111111112'
W1='EygStH4gHv1h4E8w4raYv6NGsrDiij3nbfCc26iTpump'
W2='4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'

def quote(price=0.001,mcap=15000):return Quote(MINT,price,mcap,9000,'pumpfun')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg=Config()
        self.bot=PaperBroker(self.cfg,str(Path(self.tmp.name)/'paper.json'))

    def test_modes_and_validation(self):
        self.assertEqual(self.cfg.allocation_pct,0.03)
        self.cfg.mode='heavy'; self.assertEqual(self.cfg.allocation_pct,0.15)
        self.cfg.min_distinct_wallets=1
        with self.assertRaises(AssertionError):self.cfg.validate()

    def test_dex_do_not_take_reversed_pair(self):
        pairs=[{'chainId':'solana','baseToken':{'address':W1},'quoteToken':{'address':MINT},'priceUsd':'2', 'marketCap':15000,'liquidity':{'usd':12000}}]
        self.assertIsNone(quote_from_pairs(MINT,pairs))
        pairs[0]['baseToken']['address']=MINT
        self.assertIsNotNone(quote_from_pairs(MINT,pairs))

    def test_consensus_expiry_unique_and_sell(self):
        c=Consensus(30)
        c.record(MINT,W1,'buy',100)
        c.record(MINT,W1,'buy',100)
        self.assertFalse(c.consensus(MINT,2,105))
        c.record(MINT,W2,'buy',110)
        self.assertTrue(c.consensus(MINT,2,115))
        self.assertFalse(c.consensus(MINT,2,135))
        c.record(MINT,W1,'buy',140)
        c.record(MINT,W2,'sell',141)
        self.assertFalse(c.consensus(MINT,2,142))

    def test_fomo_strict_shape(self):
        obj={'wallets':[{'walletAddress':W1},{'address':W2},{'mint':MINT},{'privateKey':W1}]}
        self.assertEqual(parse_wallet_export(obj),[W1,W2])
        self.assertEqual(parse_wallet_export({'random':{'tokenAddress':MINT}}),[])

    def test_paper_buy_sell_slippage_persist(self):
        self.assertTrue(self.bot.buy(quote(),[W1,W2],100))
        p=self.bot.positions[MINT]
        self.assertAlmostEqual(p.basis_usd,30)
        self.assertLess(p.units*quote().price_usd,30)
        sales=self.bot.update(quote(0.002),105)
        self.assertEqual(len(sales),1)
        self.assertEqual(sales[0].reason,'2x partial')
        bot2=PaperBroker(self.cfg,str(Path(self.tmp.name)/'paper.json'))
        self.assertIn(MINT,bot2.positions)
        self.assertTrue(bot2.positions[MINT].partial_2x)
        self.assertGreater(bot2.cash,970)

    def test_stop_loss_and_no_repeat(self):
        self.assertTrue(self.bot.buy(quote(),[W1,W2],100))
        sales=self.bot.update(quote(0.00080),105)
        self.assertEqual(sales[0].reason,'stop-loss')
        self.assertNotIn(MINT,self.bot.positions)
        self.assertEqual(self.bot.update(quote(0.0007),110),[])
        self.assertLess(self.bot.equity(),1000)

    def test_trailing(self):
        self.bot.buy(quote(),[W1,W2],100)
        self.bot.update(quote(0.0018),101)
        sales=self.bot.update(quote(0.0013),102)
        self.assertEqual(sales[0].reason,'trailing-stop')

    def test_stale_zero_mark_not_fake_sale(self):
        self.bot.buy(quote(),[W1,W2],100)
        sale=self.bot.data_stale(MINT,100+self.cfg.max_price_age_seconds+1)
        self.assertEqual(sale.net_usd,0)
        self.assertIn('not executable',sale.reason)

    def test_rug_fail_closed_if_rpc_wrong(self):
        class BadRPC(SolanaRPC):
            def call(self,*args):return {'value':None}
        self.assertFalse(BadRPC('https://x').examine(MINT,W1).allowed)

    def test_rug_detects_active_authority(self):
        class FakeRPC(SolanaRPC):
            def call(self,m,p):
                if m=='getAccountInfo':return {'value':{'data':{'parsed':{'type':'mint','info':{'mintAuthority':W1,'freezeAuthority':None,'supply':'1000'}}}}}
                if m=='getTokenLargestAccounts':return {'value':[{'address':W1,'amount':'100'}]}
                if m=='getMultipleAccounts':return {'value':[{'data':{'parsed':{'info':{'owner':W2}}}}]}
                raise RuntimeError(m)
        r=FakeRPC('https://x').examine(MINT,W1)
        self.assertFalse(r.allowed)
        self.assertIn('mint authority active',r.reasons)

if __name__=='__main__':unittest.main()
