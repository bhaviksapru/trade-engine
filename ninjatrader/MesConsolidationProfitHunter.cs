#region Using declarations
using System;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
using NinjaTrader.NinjaScript.Indicators;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class MesConsolidationProfitHunter : Strategy
    {
        private Bollinger bb;
        private RSI rsi;
        private int lastExitBar = -9999;

        #region Parameters
        [NinjaScriptProperty]
        [Display(Name = "BB Period", Order = 1, GroupName = "Logic")]
        public int BBPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Cooldown Bars", Order = 2, GroupName = "Logic")]
        public int CooldownBars { get; set; }

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
        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "MesConsolidationProfitHunter";
                Calculate = Calculate.OnBarClose;
                IsExitOnSessionCloseStrategy = true;
                
                // Tuned Parameters
                BBPeriod = 20;
                CooldownBars = 3;
                ProfitTargetTicks = 18; 
                StopLossTicks = 14;     
                BreakevenTriggerPct = 0.65; 
                StartTime = 094500;
                EndTime = 154500;
            }
            else if (State == State.DataLoaded)
            {
                bb = Bollinger(2.0, BBPeriod);
                rsi = RSI(14, 3);
                
                SetProfitTarget(CalculationMode.Ticks, ProfitTargetTicks);
                SetStopLoss(CalculationMode.Ticks, StopLossTicks);
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BBPeriod) return;

            // 1. Manage Existing Position
            if (Position.MarketPosition != MarketPosition.Flat)
            {
                ManageBreakeven();
                
                // Exit at the Mean (Middle Band) logic
                if (Position.MarketPosition == MarketPosition.Long && Close[0] >= bb.Middle[0])
                    ExitLong("ExitAtMean", "");
                if (Position.MarketPosition == MarketPosition.Short && Close[0] <= bb.Middle[0])
                    ExitShort("ExitAtMean", "");
                    
                return;
            }

            // 2. Cooldown and Time Filters
            if (CurrentBar - lastExitBar < CooldownBars) return;
            
            int timeNow = ToTime(Time[0]);
            if (timeNow < StartTime || timeNow > EndTime) return;

            // 3. Entry Logic
            // Long: Oversold logic
            if (Low[0] < bb.Lower[0] && rsi[0] < 30)
            {
                EnterLong("SidewaysLong");
            }

            // Short: Asymmetric logic (Strict RSI to prevent shorting strong bull trends)
            if (High[0] > bb.Upper[0] && rsi[0] > 75)
            {
                EnterShort("SidewaysShort");
            }
        }

        private void ManageBreakeven()
        {
            double triggerAmount = (ProfitTargetTicks * TickSize) * BreakevenTriggerPct;
            
            if (Position.MarketPosition == MarketPosition.Long)
            {
                if (Close[0] >= Position.AveragePrice + triggerAmount)
                {
                    SetStopLoss(CalculationMode.Price, Position.AveragePrice + (1 * TickSize));
                }
            }
            else if (Position.MarketPosition == MarketPosition.Short)
            {
                if (Close[0] <= Position.AveragePrice - triggerAmount)
                {
                    SetStopLoss(CalculationMode.Price, Position.AveragePrice - (1 * TickSize));
                }
            }
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price,
            int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order != null && execution.Order.OrderState == OrderState.Filled)
            {
                if (marketPosition == MarketPosition.Flat)
                {
                    lastExitBar = CurrentBar;
                }
            }
        }
    }
}