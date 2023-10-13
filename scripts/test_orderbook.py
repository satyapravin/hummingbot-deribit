from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.connector.utils import combine_to_hb_trading_pair

def to_bullets(strings):
    formatted_strings = [f" - {s}" for s in strings]
    return '\n' + '\n'.join(formatted_strings)

class LogPricesExample(ScriptStrategyBase):
    """
    This example shows how to get the ask and bid of a market and log it to the console.
    """
    
    base = "BTC"
    quote = "USD"
    pair = combine_to_hb_trading_pair(base, quote)

    markets = {
        "deribit": {pair}
    }

    def on_tick(self):
        for connector_name, connector in self.connectors.items():
            b = connector.get_order_book(self.pair)
            m = f"\n\nGet realtime orderbook for {self.pair}..."
            m += to_bullets([
                f"Connector: {connector_name}",
                f"Last Traded: {b.last_trade_price}",
                f"Best ask: {connector.get_price(self.pair, True)}",
                f"Best bid: {connector.get_price(self.pair, False)}",
                f"Mid price: {connector.get_mid_price(self.pair)}"
            ])

            self.logger().info(m)
            self.logger().notify(m)

