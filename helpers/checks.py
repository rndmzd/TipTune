from utils.structured_logging import get_structured_logger

logger = get_structured_logger('mongobate.helpers.checks')

class Checks:
    def __init__(self):
        from . import config

        self.config = config

        self.song_cost = self.config.getint("General", "song_cost")
        self.skip_song_cost = self.config.getint("General", "skip_song_cost")

        logger.debug("checks.init",
                    message="Initialized checks",
                    data={
                        "song_cost": self.song_cost,
                        "skip_song_cost": self.skip_song_cost
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
        is_request = tip_amount % self.song_cost == 0
        logger.debug("checks.song_request",
                    message="Checking if tip is song request",
                    data={
                        "tip_amount": tip_amount,
                        "song_cost": self.song_cost,
                        "is_request": is_request
                    })
        return is_request

    def get_request_count(self, tip_amount):
        count = tip_amount // self.song_cost
        logger.debug("checks.request_count",
                    message="Calculating song request count",
                    data={
                        "tip_amount": tip_amount,
                        "song_cost": self.song_cost,
                        "request_count": count
                    })
        return count
