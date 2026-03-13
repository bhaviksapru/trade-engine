#region Using declarations
using System;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
using NinjaTrader.NinjaScript.Indicators;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Custom.AddOns; // OrchestratorClient
#endregion

// ──────────────────────────────────────────────────────────────────────────────
// MesProfitHunterSideways — trade-engine edition
//
// What changed from the original:
//   • EnterLong / EnterShort → fire via OrchestratorClient to AWS (IBKR execution)
//   • SetProfitTarget / SetStopLoss removed — AWS set_stop Lambda handles this
//   • ManageBreakeven removed — AWS check_price Lambda handles this
//   • ExitLong / ExitShort at mean removed — AWS monitoring loop handles exits
//   • Position.MarketPosition checks removed — NinjaTrader is signal-only now,
//     it has no position state. Cooldown is tracked by lastSignalBar instead.
//   • OnExecutionUpdate removed — no local executions
//
// Everything else (BB, RSI, cooldown, time filter, all parameters) is unchanged.
// ──────────────────────────────────────────────────────────────────────────────

namespace NinjaTrader.NinjaScript.Strategies
{
    public class MesProfitHunterSideways : Strategy
    {
        private Bollinger           bb;
        private RSI                 rsi;
        private OrchestratorClient  _client;
        private int                 lastSignalBar = -9999; // replaces lastExitBar

        #region Parameters

        [NinjaScriptProperty]
        [Display(Name = "BB Period", Order = 1, GroupName = "Logic")]
        public int BBPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Cooldown Bars", Order = 2, GroupName = "Logic")]
        public int CooldownBars { get; set; }

        // Kept as reference — actual enforcement is in AWS risk_parameters DynamoDB
        [NinjaScriptProperty]
        [Display(Name = "Profit Target (ticks)", Order = 1, GroupName = "Risk")]
        public int ProfitTargetTicks { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Stop Loss (ticks)", Order = 2, GroupName = "Risk")]
        public int StopLossTicks { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "BE Trigger %", Order = 3, GroupName = "Risk")]
        public double BreakevenTriggerPct { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Start Hour (HHMMSS)", Order = 1, GroupName = "Time")]
        public int StartTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "End Hour (HHMMSS)", Order = 2, GroupName = "Time")]
        public int EndTime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Quantity (contracts)", Order = 3, GroupName = "Logic")]
        public int Quantity { get; set; }

        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "MesProfitHunterSideways";
                Calculate   = Calculate.OnBarClose;
                IsExitOnSessionCloseStrategy = true;

                // Tuned Parameters — unchanged
                BBPeriod             = 20;
                CooldownBars         = 3;
                ProfitTargetTicks    = 18;
                StopLossTicks        = 14;
                BreakevenTriggerPct  = 0.65;
                StartTime            = 094500;
                EndTime              = 154500;
                Quantity             = 1;
            }
            else if (State == State.DataLoaded)
            {
                bb  = Bollinger(2.0, BBPeriod);
                rsi = RSI(14, 3);

                // Initialise cloud execution client
                // Strategy ID is used for per-strategy risk tracking in DynamoDB
                _client = new OrchestratorClient("MesProfitHunterSideways");

                // NOTE: SetProfitTarget / SetStopLoss intentionally removed.
                // AWS set_stop Lambda places the stop at IBKR after fill.
                // AWS check_price Lambda monitors TP, stop, breakeven, and timeout.
            }
            else if (State == State.Terminated)
            {
                _client?.Dispose();
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BBPeriod) return;

            // ── Cooldown filter ──────────────────────────────────────────────
            // lastSignalBar tracks when we last FIRED a signal (not execution,
            // since execution now happens in AWS — NinjaTrader has no position state)
            if (CurrentBar - lastSignalBar < CooldownBars) return;

            // ── Time filter — unchanged ──────────────────────────────────────
            int timeNow = ToTime(Time[0]);
            if (timeNow < StartTime || timeNow > EndTime) return;

            // ── Entry signals — unchanged logic, new execution path ──────────

            // Long: price pierced lower BB + oversold RSI
            if (Low[0] < bb.Lower[0] && rsi[0] < 30)
            {
                FireBuy("SidewaysLong");
            }

            // Short: price pierced upper BB + strongly overbought RSI
            // (asymmetric threshold vs long to avoid shorting strong bull trends)
            else if (High[0] > bb.Upper[0] && rsi[0] > 75)
            {
                FireSell("SidewaysShort");
            }

            // NOTE: ExitLong / ExitShort at BB middle removed.
            // AWS monitoring_loop.asl.json handles all exits:
            //   • Stop loss hit    → close_position Lambda
            //   • Take profit hit  → close_position Lambda
            //   • Breakeven move   → set_stop Lambda (triggered at BreakevenTriggerPct)
            //   • Exit at mean     → configurable in check_price Lambda
            //   • Timeout          → close_position Lambda (MaxTradeDurationMinutes)
        }

        // ── Signal helpers ────────────────────────────────────────────────────

        private void FireBuy(string comment)
        {
            try
            {
                var result = _client.BuyAsync(
                    symbol:    Instrument.MasterInstrument.Name, // "MES"
                    quantity:  Quantity,
                    orderType: "MKT",
                    comment:   comment
                ).GetAwaiter().GetResult();

                if (result.Accepted)
                {
                    lastSignalBar = CurrentBar;
                    Print($"[TradeEngine] BUY accepted | trade_id={result.TradeId} | bar={CurrentBar}");
                }
                else
                {
                    Print($"[TradeEngine] BUY rejected: {result.Reason}");
                }
            }
            catch (Exception ex)
            {
                Print($"[TradeEngine] BUY error: {ex.Message}");
            }
        }

        private void FireSell(string comment)
        {
            try
            {
                var result = _client.SellAsync(
                    symbol:    Instrument.MasterInstrument.Name, // "MES"
                    quantity:  Quantity,
                    orderType: "MKT",
                    comment:   comment
                ).GetAwaiter().GetResult();

                if (result.Accepted)
                {
                    lastSignalBar = CurrentBar;
                    Print($"[TradeEngine] SELL accepted | trade_id={result.TradeId} | bar={CurrentBar}");
                }
                else
                {
                    Print($"[TradeEngine] SELL rejected: {result.Reason}");
                }
            }
            catch (Exception ex)
            {
                Print($"[TradeEngine] SELL error: {ex.Message}");
            }
        }
    }
}
