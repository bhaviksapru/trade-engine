// StrategyTemplate.cs — example NinjaTrader 8 strategy using OrchestratorClient
// Replace signal logic with your own. The client call pattern stays the same.
// Compile: NinjaTrader → Tools → Edit NinjaScript → Compile

using System;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
using NinjaTrader.Custom.AddOns;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class TradeEngineStrategy : Strategy
    {
        private OrchestratorClient _client;

        // ── Strategy parameters (set in NinjaTrader UI) ───────────────────────
        [NinjaScriptProperty]
        public string StrategyId { get; set; } = "MyStrategy_v1";

        [NinjaScriptProperty]
        public string Symbol { get; set; } = "ES";

        [NinjaScriptProperty]
        public int TradeQuantity { get; set; } = 1;

        [NinjaScriptProperty]
        public int FastEmaPeriod { get; set; } = 9;

        [NinjaScriptProperty]
        public int SlowEmaPeriod { get; set; } = 21;

        // ── NinjaTrader lifecycle ─────────────────────────────────────────────

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TradeEngineStrategy";
                Description = "Routes signals to trade-engine AWS backend";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = false;
            }
            else if (State == State.Configure)
            {
                // Add indicators
                AddChartIndicator(EMA(FastEmaPeriod));
                AddChartIndicator(EMA(SlowEmaPeriod));
            }
            else if (State == State.DataLoaded)
            {
                // Initialise the client — picks up BaseUrl + ApiKey from the class constants
                _client = new OrchestratorClient(StrategyId);
                Print($"[TradeEngine] Client initialized. Strategy: {StrategyId}");
            }
            else if (State == State.Terminated)
            {
                _client?.Dispose();
            }
        }

        protected override void OnBarUpdate()
        {
            // Only run on primary data series, skip warm-up bars
            if (BarsInProgress != 0 || CurrentBar < SlowEmaPeriod) return;

            var fastEma = EMA(FastEmaPeriod)[0];
            var slowEma = EMA(SlowEmaPeriod)[0];
            var prevFast = EMA(FastEmaPeriod)[1];
            var prevSlow = EMA(SlowEmaPeriod)[1];

            bool crossedAbove = prevFast <= prevSlow && fastEma > slowEma;
            bool crossedBelow = prevFast >= prevSlow && fastEma < slowEma;

            // ── ✏️ YOUR SIGNAL LOGIC GOES HERE ───────────────────────────────
            // This EMA crossover is just an example. Replace with your strategy.

            if (crossedAbove)
            {
                Print($"[TradeEngine] BUY signal: {Symbol} x{TradeQuantity}");
                FireBuy(Symbol, TradeQuantity, "EMA_CROSS_ABOVE");
            }
            else if (crossedBelow)
            {
                Print($"[TradeEngine] SELL signal: {Symbol} x{TradeQuantity}");
                FireSell(Symbol, TradeQuantity, "EMA_CROSS_BELOW");
            }
        }

        // ── Signal helpers ────────────────────────────────────────────────────

        private void FireBuy(string symbol, int qty, string comment = null)
        {
            try
            {
                var result = _client.BuyAsync(symbol, qty, comment: comment)
                                    .GetAwaiter().GetResult();

                if (result.Accepted)
                    Print($"[TradeEngine] BUY accepted | trade_id={result.TradeId}");
                else
                    Print($"[TradeEngine] BUY rejected: {result.Reason}");
            }
            catch (Exception ex)
            {
                Print($"[TradeEngine] BUY error: {ex.Message}");
            }
        }

        private void FireSell(string symbol, int qty, string comment = null)
        {
            try
            {
                var result = _client.SellAsync(symbol, qty, comment: comment)
                                    .GetAwaiter().GetResult();

                if (result.Accepted)
                    Print($"[TradeEngine] SELL accepted | trade_id={result.TradeId}");
                else
                    Print($"[TradeEngine] SELL rejected: {result.Reason}");
            }
            catch (Exception ex)
            {
                Print($"[TradeEngine] SELL error: {ex.Message}");
            }
        }
    }
}
