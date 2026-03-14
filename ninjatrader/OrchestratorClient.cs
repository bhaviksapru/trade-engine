// OrchestratorClient.cs
// Place in: Documents\NinjaTrader 8\bin\Custom\
// Compile:  NinjaTrader → Tools → Edit NinjaScript → Compile
//
//   UPDATE LINES 16-20 WITH YOUR VALUES
//     Run: cd infra && terraform output

using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

namespace NinjaTrader.Custom.AddOns
{
    public class OrchestratorClient : IDisposable
    {
        // ──  UPDATE THESE TWO LINES ──────────────────────────────────────────
        private const string BaseUrl = "https://XXXX.execute-api.us-east-2.amazonaws.com/prod";
        // terraform output: api_gateway_url  (no trailing slash, no /signal)

        private const string ApiKey = "PASTE_API_KEY_HERE";
        // aws secretsmanager get-secret-value --secret-id trade-engine/api-key
        // ───────────────────────────────────────────────────────────────────────

        private readonly HttpClient _http;
        private readonly string     _strategyId;

        private static readonly JsonSerializerOptions _json = new()
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            PropertyNameCaseInsensitive = true,
        };

        public OrchestratorClient(string strategyId)
        {
            _strategyId = strategyId;
            _http       = new HttpClient { BaseAddress = new Uri(BaseUrl.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(10) };
            _http.DefaultRequestHeaders.Add("X-API-Key", ApiKey);
            _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        }

        public Task<OrderResponse> BuyAsync(string symbol, int quantity, string orderType = "MKT", double? limitPrice = null, string comment = null)
            => SendAsync(symbol, "BUY", quantity, orderType, limitPrice, comment);

        public Task<OrderResponse> SellAsync(string symbol, int quantity, string orderType = "MKT", double? limitPrice = null, string comment = null)
            => SendAsync(symbol, "SELL", quantity, orderType, limitPrice, comment);

        private async Task<OrderResponse> SendAsync(string symbol, string side, int quantity, string orderType, double? limitPrice, string comment)
        {
            var body = new StringContent(JsonSerializer.Serialize(new OrderRequest
            {
                StrategyId = _strategyId, Symbol = symbol, Side = side,
                Quantity = quantity, OrderType = orderType, LimitPrice = limitPrice, Comment = comment,
            }, _json), Encoding.UTF8, "application/json");

            try
            {
                var resp = await _http.PostAsync("signal", body);
                var raw  = await resp.Content.ReadAsStringAsync();
                if (!resp.IsSuccessStatusCode) return Reject($"HTTP {(int)resp.StatusCode}: {raw}");
                return JsonSerializer.Deserialize<OrderResponse>(raw, _json) ?? Reject("Empty response");
            }
            catch (TaskCanceledException) { return Reject("Timeout"); }
            catch (Exception ex)          { return Reject($"Network: {ex.Message}"); }
        }

        private static OrderResponse Reject(string r) => new() { Status = "rejected", Reason = r };
        public void Dispose() => _http?.Dispose();
    }

    public class OrderRequest
    {
        [JsonPropertyName("strategy_id")]  public string  StrategyId { get; set; }
        [JsonPropertyName("symbol")]       public string  Symbol     { get; set; }
        [JsonPropertyName("side")]         public string  Side       { get; set; }
        [JsonPropertyName("quantity")]     public int     Quantity   { get; set; }
        [JsonPropertyName("order_type")]   public string  OrderType  { get; set; } = "MKT";
        [JsonPropertyName("limit_price")]  public double? LimitPrice { get; set; }
        [JsonPropertyName("comment")]      public string  Comment    { get; set; }
    }

    public class OrderResponse
    {
        [JsonPropertyName("status")]        public string Status       { get; set; }
        [JsonPropertyName("trade_id")]      public string TradeId      { get; set; }
        [JsonPropertyName("execution_arn")] public string ExecutionArn { get; set; }
        [JsonPropertyName("reason")]        public string Reason       { get; set; }
        public bool Accepted => Status == "accepted";
        public bool Rejected => Status == "rejected";
    }
}
