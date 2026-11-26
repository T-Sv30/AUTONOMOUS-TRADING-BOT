class RegimeServiceV2:
    WINDOW = 20
    RANGE_ER = 0.30

    def classify(self, prices):
        if len(prices) < 30:
            return "unknown"

        window = prices[-(self.WINDOW + 1) :]
        path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
        if path == 0:
            return "range"

        net = window[-1] - window[0]
        if abs(net) / path < self.RANGE_ER:
            return "range"
        return "trend_up" if net > 0 else "trend_down"
