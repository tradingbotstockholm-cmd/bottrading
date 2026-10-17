"""Optional bridge to ajagatobby/fomo CLI (installed separately on macOS)."""
from __future__ import annotations
import json, re, subprocess
from pathlib import Path

B58=re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
WALLET_FIELDS=('walletAddress','wallet_address','wallet','address','solanaWallet','solana_address','publicKey','pubkey')

def parse_wallet_export(raw: object, limit: int=20) -> list[str]:
    """Conservative format adapter. Unknown schemas => zero copied wallets."""
    found=[]
    def add(v):
        if isinstance(v,str) and B58.fullmatch(v) and v not in found:
            found.append(v)
    def walk(node, depth=0):
        if depth>4 or len(found)>=limit: return
        if isinstance(node,list):
            for item in node[:100]: walk(item,depth+1)
        elif isinstance(node,dict):
            for k in WALLET_FIELDS:
                v=node.get(k)
                if isinstance(v,str): add(v)
                elif isinstance(v,(dict,list)): walk(v,depth+1)
            for k in ('wallets','data','leaderboard','results','traders','profiles','items','linkedWallets'):
                if k in node: walk(node[k],depth+1)
    walk(raw)
    return found[:limit]

def from_file(path: str, limit: int=20) -> list[str]:
    p=Path(path)
    return parse_wallet_export(json.loads(p.read_text()),limit) if p.exists() else []

def from_cli(limit: int=20) -> list[str]:
    """Requires fomo login; never reads Keychain or cookies directly."""
    cp=subprocess.run(['fomo','wallets','--window','7d','--top',str(limit),'--json'],
                      capture_output=True,text=True,timeout=45,check=True,env=None)
    return parse_wallet_export(json.loads(cp.stdout),limit)
