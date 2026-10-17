"""Basic on-chain risk checks, not a guarantee against rugs or wash trading."""
from __future__ import annotations
from .http import post_json
from .models import RiskResult

class SolanaRPC:
    def __init__(self, endpoint: str):
        self.endpoint=endpoint

    def call(self, method: str, params: list):
        from hunter2.chain import shared_client
        return shared_client(self.endpoint).call(method,params,transport=post_json)

    def examine(self, mint: str, bonding_curve_owner: str | None=None) -> RiskResult:
        reasons=[]
        try:
            info=self.call('getAccountInfo',[mint,{'encoding':'jsonParsed','commitment':'confirmed'}])
            data=info['value']['data']['parsed']
            if data.get('type')!='mint':
                return RiskResult(False,['account is not a parsed SPL mint'])
            fields=data['info']
            if fields.get('mintAuthority') is not None:
                reasons.append('mint authority active')
            if fields.get('freezeAuthority') is not None:
                reasons.append('freeze authority active')
            if int(fields.get('supply',0))<=0:
                reasons.append('token supply is zero')

            largest=self.call('getTokenLargestAccounts',[mint,{'commitment':'confirmed'}])
            values=largest['value'][:10]
            total=int(fields['supply'])
            accounts=[str(v['address']) for v in values]
            owners={}
            if accounts:
                blocks=self.call('getMultipleAccounts',[accounts,{'encoding':'jsonParsed','commitment':'confirmed'}])
                for addr,acct in zip(accounts,blocks['value']):
                    try:
                        owners[addr]=acct['data']['parsed']['info']['owner']
                    except (KeyError, TypeError):
                        return RiskResult(False,['cannot resolve token account ownership'])
            if not bonding_curve_owner:
                return RiskResult(False,['no verified bonding curve owner for concentration check'])
            non_curve=sum(int(v['amount']) for v in values if owners.get(str(v['address'])) != bonding_curve_owner)
            share=non_curve/total if total>0 else 1
            if share>0.65:
                reasons.append(f'large non-curve token accounts: {share:.1%} of supply')
            return RiskResult(not reasons, reasons, share)
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as e:
            return RiskResult(False,reasons+[f'malformed RPC response: {type(e).__name__}'])
        except Exception as e:
            return RiskResult(False,reasons+[f'risk data unavailable: {type(e).__name__}: {e}'])
