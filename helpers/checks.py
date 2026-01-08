from utils.structured_logging import get_structured_logger

logger = get_structured_logger('tiptune.helpers.checks')

class Checks:
    def __init__(self):
        from . import config

        self.config = config

        self.song_cost = 27
        self.skip_song_cost = 51
        self.multi_request_tips = True

        try:
            self.song_cost = self.config.getint("General", "song_cost", fallback=self.song_cost)
        except Exception:
            pass

        try:
            self.skip_song_cost = self.config.getint("General", "skip_song_cost", fallback=self.skip_song_cost)
        except Exception:
            pass

        try:
            raw = self.config.get("General", "multi_request_tips", fallback="true")
            s = str(raw).strip().lower()
            self.multi_request_tips = not (s == "false" or s == "0" or s == "no" or s == "off")
        except Exception:
            pass

        if not isinstance(self.song_cost, int) or self.song_cost <= 0:
            self.song_cost = 27
        if not isinstance(self.skip_song_cost, int) or self.skip_song_cost <= 0:
            self.skip_song_cost = 51

        logger.debug("checks.init",
                    message="Initialized checks",
                    data={
                        "song_cost": self.song_cost,
                        "skip_song_cost": self.skip_song_cost,
                        "multi_request_tips": self.multi_request_tips,
                    })

    def is_skip_song_request(self, tip_amount):
        is_skip = tip_amount % self.skip_song_cost == 0
        logger.debug("checks.skip_song",
                    message="Checking if tip is skip song request",
                    data={
                        "tip_amount": tip_amount,
                        "skip_cost": self.skip_song_cost,
                        "is_skip": is_skip
                    })
        return is_skip

    def is_song_request(self, tip_amount):
        if not isinstance(tip_amount, int) or tip_amount <= 0:
            is_request = False
        elif self.multi_request_tips:
            is_request = tip_amount % self.song_cost == 0
        else:
            is_request = tip_amount == self.song_cost
        logger.debug("checks.song_request",
                    message="Checking if tip is song request",
                    data={
                        "tip_amount": tip_amount,
                        "song_cost": self.song_cost,
                        "multi_request_tips": self.multi_request_tips,
                        "is_request": is_request
                    })
        return is_request

    def get_request_count(self, tip_amount):
        if not isinstance(tip_amount, int) or tip_amount <= 0:
            count = 0
        elif self.multi_request_tips:
            count = tip_amount // self.song_cost
        else:
            count = 1 if tip_amount == self.song_cost else 0
        logger.debug("checks.request_count",
                    message="Calculating song request count",
                    data={
                        "tip_amount": tip_amount,
                        "song_cost": self.song_cost,
                        "multi_request_tips": self.multi_request_tips,
                        "request_count": count
                    })
        return count
