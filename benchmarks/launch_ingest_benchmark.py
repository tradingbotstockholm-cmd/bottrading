"""CPU microbenchmark, not evidence of live chain throughput or profitable trading.
Run from project root: python benchmarks/launch_ingest_benchmark.py --events 100000
"""
import argparse,json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hunter2.faststream import launch_event

MINT='9oC3XYAs2oeU39NeNFke8m3JGixMq7g8PfANsmsbKR8W'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--events',type=int,default=100000)
    options=parser.parse_args()
    if not 100<=options.events<=2000000:parser.error('events must be 100–2,000,000')
    raw=json.dumps({'txType':'create','mint':MINT,'traderPublicKey':MINT,'bondingCurveKey':MINT})
    start=time.perf_counter();recognized=0
    for _ in range(options.events):
        if launch_event(json.loads(raw)) is not None:recognized+=1
    duration=time.perf_counter()-start
    print('Synthetic JSON decoding and launch-event filtering only.')
    print(f'Events checked: {options.events:,}; recognized: {recognized:,}; elapsed: {duration:.3f}s')
    print(f'Measured local parse rate: {options.events/duration:,.0f} events/s')
    print('Not network, token risk analysis, swaps, blockchain throughput, or executable orders.')

if __name__=='__main__':main()
