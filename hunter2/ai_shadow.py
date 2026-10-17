"""Optional pretrained Chronos-Bolt Tiny PRICE FORECAST shadow monitor.

No buy/sell logic depends on forecasts. Real model weights and CPU libraries
must be installed by the operator, separately; do not call this a memecoin-
trained model or interpret forecasting quantiles as trading probabilities.
"""
from __future__ import annotations
import argparse,math,time,json
from hunter2.db import Database
MODEL_ID='amazon/chronos-bolt-tiny'

def forecast_quantiles(pipeline,prices,prediction_length=3):
    """Return p10/p50/p90 last-horizon *relative prices*, not PnL."""
    if len(prices)<32 or not all(math.isfinite(x) and x>0 for x in prices):
        return None
    import torch
    context=torch.tensor(prices,dtype=torch.float32)
    # In the Chronos-Bolt API, output is [batch, quantile, horizon].
    forecasts=pipeline.predict(context=context,prediction_length=prediction_length)
    tensor=forecasts.detach().cpu()
    if tensor.ndim!=3 or tensor.shape[0]!=1 or tensor.shape[1]<7:return None
    last=float(prices[-1])
    result=tuple(float(tensor[0,i,-1])/last-1 for i in (0,4,8)) if tensor.shape[1]>=9 else None
    return result if result is not None and all(math.isfinite(x) for x in result) else None

def monitor(db_path,interval=90,max_mints=5,once=False):
    try:
        from chronos import BaseChronosPipeline
        import torch
    except ImportError as ex:
        raise SystemExit('Optional Chronos not installed. See AI_SETUP.md. Paper bot still runs without AI.') from ex
    pipeline=BaseChronosPipeline.from_pretrained(MODEL_ID,device_map='cpu',torch_dtype=torch.float32)
    db=Database(db_path)
    try:
        while True:
            with db.lock:
                mints=[x[0] for x in db.conn.execute('SELECT mint FROM price_samples GROUP BY mint HAVING COUNT(*)>=32 ORDER BY MAX(observed) DESC LIMIT ?', (max_mints,)).fetchall()]
            for mint in mints:
                data=db.recent_price_series(mint,128)
                try:
                    pred=forecast_quantiles(pipeline,data)
                    if pred is not None:
                        db.record_ai_shadow(mint,*pred,len(data),MODEL_ID)
                except Exception as e:db.record_error('ai_shadow',type(e).__name__)
            if once:break
            time.sleep(interval)
    finally:db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='config.v4.json');parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    cfg=json.load(open(args.config))
    monitor(cfg['db_path'],once=args.once)
