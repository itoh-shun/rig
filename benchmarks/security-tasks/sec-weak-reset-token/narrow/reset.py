import random


def new_reset_token():
    # Still the Mersenne Twister PRNG, just fewer calls to it.
    return "%032x" % random.getrandbits(128)
