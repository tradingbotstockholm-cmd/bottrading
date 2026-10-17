"""Wallet identities are public addresses only: NEVER paste wallet secrets here."""
from __future__ import annotations
import json,re
from pathlib import Path
from dataclasses import dataclass,asdict
from hunter2.chain import SolanaClient

ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
ROLE_NAMES=('phantom_treasury','fomo_app','pumpfun_connected','pumpportal_trading')

@dataclass(frozen=True)
class WalletAccounts:
    phantom_treasury: str = ''
    fomo_app: str = ''
    pumpfun_connected: str = ''
    pumpportal_trading: str = ''
    def validate(self):
        for name in ROLE_NAMES:
            value=getattr(self,name)
            if value and not (isinstance(value,str) and ADDRESS.fullmatch(value)):
                raise ValueError(f'Invalid public Solana address for {name}')
    @classmethod
    def load(cls,filename='accounts.json'):
        raw=json.loads(Path(filename).read_text())
        if set(raw)-set(ROLE_NAMES):raise ValueError('Only public wallet addresses permitted in accounts.json')
        obj=cls(**raw);obj.validate();return obj
    def save(self,filename='accounts.json'):
        self.validate()
        path=Path(filename)
        if path.exists():raise FileExistsError('Refusing to overwrite account address file')
        path.write_text(json.dumps(asdict(self),indent=2)+'\n')


def validate_funding_separation(accounts:WalletAccounts,trader_address:str):
    """Fail if a bot API wallet is confused with a primary or FOMO wallet."""
    accounts.validate()
    if not trader_address or accounts.pumpportal_trading!=trader_address:
        raise ValueError('live.json address does not match accounts.json pumpportal_trading')
    if trader_address in (accounts.phantom_treasury,accounts.fomo_app):
        raise ValueError('Live trading must not use treasury or FOMO app wallet')
    if not accounts.phantom_treasury or not accounts.fomo_app:
        raise ValueError('Add Phantom and FOMO public addresses before live activation')

def balances(accounts:WalletAccounts,rpc:SolanaClient)->list[dict]:
    accounts.validate()
    records=[]
    for role in ROLE_NAMES:
        addr=getattr(accounts,role)
        if not addr: records.append(dict(role=role,connected=False,address='',sol=None,error='address not configured'));continue
        try:
            response=rpc.call('getBalance',[addr,{'commitment':'confirmed'}])
            lamports=response['value']
            if type(lamports) is not int or lamports<0:raise ValueError('invalid balance response')
            records.append(dict(role=role,connected=True,address=addr,sol=round(lamports/1_000_000_000,9),error=''))
        except Exception as e:
            records.append(dict(role=role,connected=False,address=addr,sol=None,error=type(e).__name__))
    return records
