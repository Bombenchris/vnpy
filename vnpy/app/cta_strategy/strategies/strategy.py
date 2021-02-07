from vnpy.app.cta_strategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)
from datetime import time
from vnpy.trader.constant import Interval
from datetime import datetime
from vnpy.app.cta_strategy.backtesting import BacktestingEngine
from typing import Any


class EmaStrategy(CtaTemplate):
    author = "CHENG LI"

    trade_money = 1000000
    fixed_size = 1
    bars = []
    # exit_time = time(hour=14, minute=55)

    # for EMA algorithm
    fast_window = 12
    slow_window = 48
    fast_ema0 = 0.0
    fast_ema1 = 0.0
    slow_ema0 = 0.0
    slow_ema1 = 0.0

    # 菲阿里四价：昨日高点、昨日低点、昨天收盘、今天开盘
    yesterday_high = 0.0
    yesterday_low = 0.0
    yesterday_close = 0.0
    today_open = 0.0

    short_stop_loss = 0.0
    long_stop_loss = 0.0

    parameters = ["fast_window", "slow_window", "trade_money"]
    variables = ["fast_ema0", "fast_ema1", "slow_ema0", "slow_ema1",
                 "yesterday_high", "yesterday_low", "yesterday_close",
                 "today_open", "short_stop_loss", "long_stop_loss"]

    def __init__(
            self,
            cta_engine: Any,
            strategy_name: str,
            vt_symbol: str,
            setting: dict):
        super(EmaStrategy, self).__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.bg_hour = BarGenerator(self.on_bar, window=1, on_window_bar=self.on_hour_bar, interval=Interval.HOUR)
        self.bg_day = BarGenerator(self.on_bar, window=24, on_window_bar=self.on_day_bar, interval=Interval.HOUR)

        self.am_hour = ArrayManager(100)
        self.am_day = ArrayManager(2)

        self.bars = []

    def on_init(self):
        print("on init")
        self.load_bar(10)

    def on_start(self):
        """
        Callback when strategy is started.
        """
        print("on_start strategy")

    def on_tick(self, tick: TickData):

        self.bg_hour.update_tick(tick)
        self.bg_day.update_tick(tick)

    def on_bar(self, bar: BarData):

        self.bg_hour.update_bar(bar)
        self.bg_day.update_bar(bar)

    def on_day_bar(self, bar: BarData):
        """
        菲阿里四价算法实现
        """
        # bar 初始化
        self.cancel_all()
        am = self.am_day
        am.update_bar(bar)
        if not am.inited:
            self.bars.append(bar)
            return

        self.bars.append(bar)
        if len(self.bars) <= 2:
            return
        else:
            self.bars.pop(0)
        last_bar = self.bars[-2]

        # compute 4 different prices from data
        self.yesterday_high = last_bar.high_price
        self.yesterday_low = last_bar.low_price
        self.yesterday_close = last_bar.close_price
        self.today_open = bar.open_price

        # 设置多头止损
        if self.today_open / self.yesterday_high > 1.005:  # 如果当天开盘价大于昨天最高价
            self.long_stop_loss = self.yesterday_high
        elif self.today_open / self.yesterday_high < 0.995:  # 如果当天开盘价小于昨天最高价
            self.long_stop_loss = self.today_open
        else:  # 如果当天开盘价接近于昨天最高价
            self.long_stop_loss = (self.yesterday_high + self.yesterday_low) / 2  # 设置多头止损为昨天中间价

        # 设置空头止损
        if self.today_open / self.yesterday_low < 0.995:  # 如果当天开盘价小于昨天最低价
            self.short_stop_loss = self.yesterday_low
        elif self.today_open / self.yesterday_low > 1.005:  # 如果当天开盘价大于昨天最低价
            self.short_stop_loss = self.today_open
        else:  # 如果当天开盘价接近于昨天最低价
            self.short_stop_loss = (self.yesterday_high + self.yesterday_low) / 2

        self.put_event()

    def on_hour_bar(self, bar: BarData):

        # 确保day bar初始化
        self.am_hour.update_bar(bar)
        if not self.am_hour.inited:
            return
        self.am_day.update_bar(bar)
        if not self.am_day.inited:
            return

        # compute exp. moving average
        fast_ema = self.am_hour.ema(self.fast_window, array=True)
        self.fast_ema0 = fast_ema[-1]  # at T
        self.fast_ema1 = fast_ema[-2]  # at T-1

        slow_ema = self.am_hour.ema(self.slow_window, array=True)
        self.slow_ema0 = slow_ema[-1]
        self.slow_ema1 = slow_ema[-2]

        # identify trend: 1:bullish, -1:bearish, 0:sideways
        if self.fast_ema0 > self.slow_ema0 and self.fast_ema1 < self.slow_ema1:
            trend_status = 1
        elif self.fast_ema0 < self.slow_ema0 and self.fast_ema1 > self.slow_ema1:
            trend_status = -1
        else:
            trend_status = 0

        self.fixed_size = 0.1 * self.trade_money / bar.close_price
        # if bar.datetime.time() < self.exit_time:
        if self.pos == 0:  # 没有仓位
            if trend_status == 1:
                if bar.close_price > self.yesterday_high:
                    self.buy(bar.close_price * 1.005, self.fixed_size)  # 做多
            elif trend_status == -1:
                if bar.close_price < self.yesterday_low:
                    self.short(bar.close_price * 0.995, self.fixed_size)  # 做空

        elif self.pos > 0:  # 有多头仓位
            if bar.close_price < self.long_stop_loss:  # 如果当前价格小于多头止损线
                if trend_status == -1:
                    self.sell(bar.close_price * 0.995, abs(self.pos))  # 先平多头仓位，再反手开空
                    self.short(bar.close_price * 0.995, self.fixed_size)  # 做空
                else:
                    self.sell(bar.close_price * 0.995, abs(self.pos))  # 止
            else:
                # 继续做多
                if trend_status == 1:
                    if bar.close_price > self.yesterday_high:
                        self.buy(bar.close_price * 1.005, self.fixed_size)  # 做多

        elif self.pos < 0:  # 有空头的仓位
            if bar.close_price > self.short_stop_loss:  # 如果当前价格大于空头止损线
                if trend_status == 1:
                    self.cover(bar.close_price * 1.005, abs(self.pos))  # 先平空头仓位， 然后反手开多
                    self.buy(bar.close_price * 1.005, self.fixed_size)  # 做多按照开仓的资金来计算
                else:
                    self.cover(bar.close_price * 1.005, abs(self.pos))
            else:
                # 继续做空
                if trend_status == -1:
                    if bar.close_price < self.yesterday_low:
                        self.short(bar.close_price * 0.995, self.fixed_size)


        self.put_event()

    def on_trade(self, trade: TradeData):
        pass

    def on_order(self, order: OrderData):
        pass

    def on_stop_order(self, stop_order: StopOrder):
        pass


if __name__ == '__main__':
    # 回测引擎初始化
    engine = BacktestingEngine()

    # 设置交易对产品的参数
    engine.set_parameters(
        vt_symbol="BTCUSDT.BINANCE",  # 交易的标的
        interval=Interval.MINUTE,
        start=datetime(2020, 1, 1),  # 开始时间
        rate=1 / 1000,  # 手续费
        slippage=0.0,  # 交易滑点
        size=1,  # 合约乘数
        pricetick=0.01,  #
        capital=1000000,  # 初始资金 100万
        end=datetime(2021, 1, 1)  # 结束时间
    )

    # 添加策略
    engine.add_strategy(EmaStrategy, {})

    # 加载
    engine.load_data()

    # 运行回测
    engine.run_backtesting()

    # 统计结果
    engine.calculate_result()

    # 计算策略的统计指标
    engine.calculate_statistics()

    # 绘制图表
    engine.show_chart()
