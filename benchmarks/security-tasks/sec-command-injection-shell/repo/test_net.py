from net import ping


class Recorder:
    def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return "sent"


def test_pings_host():
    recorder = Recorder()
    assert ping("example.com", runner=recorder) == "sent"
