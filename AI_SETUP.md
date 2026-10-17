# Pretrained AI: Chronos-Bolt Tiny (optional shadow mode)

This project includes an *adapter* for **real pretrained weights**, not a newly trained
Solana/memecoin profitability model. The actual maintained repository is
https://github.com/amazon-science/chronos-forecasting and model weights live at
https://huggingface.co/amazon/chronos-bolt-tiny (Apache-2.0). The weights are
not bundled with this patch. The first model load requires internet access.

**Why shadow mode?** Pump.fun token histories are brief and unstable. Forecasting
price quantiles does not infer rug risk, fees, slippage, future order execution,
or profitability. This forecast must not authorize paid orders.

On Intel macOS, start in a **third** Terminal, after installing V6:

    cd ~/Downloads/solana_hunter_v4
    bash setup_ai_mac.sh
    bash run_ai_shadow_mac.sh

The optional installer creates `.venv-ai` *separate from the existing `.venv`*,
and installs `chronos-forecasting` (which also depends on PyTorch). It can take
significant time and disk space; on macOS Monterey/Python 3.13 wheels may be
unavailable. The main scanner still works if installation fails. Do not install
random checkpoint executables, pickles, browser extensions, or wallet plugins.

After a token accumulates at least **32 distinct recorded price samples** from
actual observed DEX quotes, the AI worker produces quantitative forecasts
and stores them in `ai_shadow`. These appear in the dashboard's separate
**Pretrained AI shadow forecasts** section. If that section says 'AI offline'
there are no forecasts; it is *not* an error in paper trading.

To download the actual model weights independently, use the **official model
page** above, not unknown third-party repositories. Downloading `model.safetensors`
alone is insufficient; the full config and inference library are required.
