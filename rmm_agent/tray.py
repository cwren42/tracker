#!/usr/bin/env python3
"""
Cirque IT Tray App
==================
System tray icon for end-users.  Provides:
  - Submit IT Ticket
  - Open IT Portal
  - Computer Info
  - Pending Updates / Reboot
  - Exit

Config is read from C:\\CirqueRMM\\tray_config.json (written by agent_client.py).

Dependencies (installed by the agent):
  pip install pystray pillow

Launched at user login via a shortcut in:
  %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\CirqueTray.lnk
"""

import base64
import ctypes
import io
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import traceback
import urllib.request
import webbrowser

# ── logging ──────────────────────────────────────────────────────────────────
_LOG_FILE = r'C:\CirqueRMM\tray.log'
def _log(msg: str) -> None:
    """Append a timestamped line to tray.log (best-effort)."""
    try:
        import datetime as _dt
        ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(_LOG_FILE, 'a', encoding='utf-8') as _fh:
            _fh.write(f'[{ts}] {msg}\n')
    except Exception:
        pass

# ── icon data (embedded at build time) ─────────────────────────────────────
_ICO_B64 = """AAABAAQAEA8AAAAAIABIAwAARgAAACAeAAAAACAA9ggAAI4DAAAwLQAAAAAgAAwPAACEDAAAQDwAAAAAIAD9FQAAkBsAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAADwgGAAAA7XNPLwAAAw9JREFUeJxNk01oXGUUht/zffcnNf2JGjNzJ9IaaAmZubcVppWAlIIUBBFStXeMCsWFSIxZuOmiUKpDVboSsqgEuxPU8d6JiKK4E+sPgpUUZuYqpZJNmcRGktZO5+fO933HRRLM4sDLORwOL+95AAAMEAO0mDk8UvWCr68VizYDhM3C24AAgCjnf1kdnTjEAG33BADECAUBrATPerb77F/N9FUCOEIoIoSyDJiK55/KSneKjXyLAC4gJGDzsgDAiyPBGFmU2EC3D+5roccbt5I7AFB4dNKV5n7DJnpYMfamzMXpldpShFCIGCERwEbyPMDdljEnLRLDWouLZcCUAaNN6+weYY21wU8bmGWL8OGm8y2PFa/wZMZyf7qt0zdebNYX4lzh/X3COXdH9w44lqscw8sto66UVupzcbbw/CP2wOJtnU6VmrWvBABYJBY2tPozadY/ivJ5Z7Dbu9Qxep1IzivDg64QjgJuAEC42vjiH5X+YoMuX0PRFpHnzw0K6adaz5YBU0qS9Jn1m/+2oWez0jmVarVvTaWXM5YzXx19/BAAGNJvWkSjy17vgkWgd+9pVZ/+u/E9AxTnjhwZlvKFNvU/WFP9Xx+Q4mNn18Akd/v3lNYeAzepmVyveP4PrhDnBQu8s1tafpT1TxDAEFjrsDnf7mOhQ/0zQ8KauHu/debkraVz06u1qwBQyRz2dwt5XDFfIgCo5oJEAOa5Zi0ggD/z8i971q5PNlR6NCWeGYR8pW3oAB5K75aSJK3m/KsS4mB/SD0mAEAZzDwo7ULkBa8BgGQ51mUNI6nn2PIsCXKNUO+VkiSNvWBqWLrHu0bPlZIkpQihLCHWUc7/VkJMdqCe2kP2UlvrKy+t1l/firk8JO0L60odswVVCNg43awdixDK/z9xdOIgsdWwIFoazB22x2+s/L4OAEWvONCn9A8btFcThnpaPzG92vgtQigBANsi9vyLP+8/yp9n/ZlNiE5Y27MoG5z+cX+RYy9Y2LmDnTR+OuJnFnPBd1E+7+ykcUsj9oJvolwwvpPG/wAwI18u8SezUAAAAABJRU5ErkJggolQTkcNChoKAAAADUlIRFIAAAAgAAAAHggGAAAATQocKQAACL1JREFUeJyVV22MVOUVfs5573yvWI0IMyxobVrr7s6CkliiNQNtolZtI+CdalITTdoYbfphXYql0tkJVUQgmGriV9NYqR/MXSyS9oftD9jWxKolcT9mEQONis4CGxTdj5m5977n9MfMbJd116Tn5819zznvOc95zvMC81gJrgGAPensi16mu2fmty+y1j99me7v9mWyBwGgAPB8/9N8TvLw7N509ooo86G6yom4g463Phj6FACKgMznr9QKlu567QInuuq0BHn3oyGv5XP2gXkzAwBL+qgCSLJZPBHo5iIgnXDnTBoACsiZPGCR7r6jzTirzthQSLHj1UXdqTI81Tku/LkEWpl6S7JuGzvX1NQGVbE2RnzPvkz20jw80TnONZz3S+mSlecy6RZfRQKVoI3Nsk+N9BQB6UXucy3k2U7K8HR/emUSqtt8FXGIIwKYKFOsrroDgHpzVMGDy0VAtFbflGKT9lUQY45NirURcM/+dMeyXvTb2XjguZzU1L83xc6XFYBVPayK0bqoJNjc5KU7r83DszMBqQC78KRvyYqvRsA/rYkIQJO+6r+JyESJ22rEWwnQ2S2cTqDQdLJ3SbbdMDZOiRUHxDaQ2wgoJJnZQhVEO57CygjgAc2eenCJALVqH4kQxWPMDNXnjKUboahV1UqU+DYv03nV7OSnE+hsOglFH4wRnxMj5qrYvlvHygMT0c92j9vwiCqQYid7Xsb/UR6wBeRMCzOlJd3fShDf7KtIVeynlnTb+pODpwKVHUk2DIBUaedsIFIDeDB5wHqLur/hOHjdqior6mLRNXRq6L0iIKVM9nsJ4ld8FVHgNCO4bKDyzifTF0h3HYoyr4gRY9zajfnRoUcOIOeMLRyLc8SUCbQ0zkwTYXj7908M/6mAnFNEf9isgNvopZGdBFCSDfuku245NfSf1chxCa7JV4b2VzX8e4SYE2wWhnA2FwEpAtKZ7vpxks0KATBh7QBM6rECwO9igvJjIxOiuinOTDWxykQPHVjY0Qb0iwLEBeScPDy7J529LcnO1VZVp6x8GNRq2woAr0a/bfYbRNRjVcOqWImA7y61Z7MKEAFXEQgREEQxnP/wX9VOuHQXDgUluMYdHX5h0trXDRGljFl6yuGNrbHkXvTLc4u6U0zYWhfROBuypA/c/vHRz1q4yAO2BNe4Hw0PBirPJNiwIYrC6iMEqCEu1NRW6yoaY7r15fblV84EGwEK0vsA6JRYiTD94sVFyy/uRb9lAiRu9N4Um4sMgSZt+OZIZWh3AxcN6mwAx0MJMEa4tyr240BFEmyu9zKdN62rDL4bQh9LsCEiMoHYHY3GdmgrkXxl+PW66gtxYo6Ck8bYBwlQ6lt82UXEzgCABRFiqolekx8dfK0FzNmEAwAvpbM/X2DMrrqKhqqHU8nqFfUglbCBlB2iTJQYEzb8QX50+PkSXFOGp72A7st0tiv4sAJJAqm19tssxnncEJ0bbYDk2UZw96zgpfaO859flj2v1N5xPgAsSFWfmBR7WAGkyHRMTCbuXvv+wBkFNhnQeF1kgkG/fCq9MunCk2KDPXltpXw8UGyJsSEFSJmeZlLEpucR5M+8aZM2CcJ/OCfkY3GJHC1lun5zw9GjdQXuj4CoqlYM4delxSsWupWhP56p1y8V3/+a4zjfqYy2+QQoAC3jFDWZxydtkQBF6eWlnV9Ry28rkHKIKYCucj8afKMFoDw866Wz18SY/2GhEEWNAtu1bqx8rJTpejVO5lpDhEmxj+crQz+Zq2Ut/r986eWLrQ0PAziHiSgUex2vO14+ZhW7kmyIAKjKztbBFoDc0aF/1sTudkCIEsVDhx5uXAAbrErQGEvc9VJ7d1cB4APIOTMZrxMuFQHxw3BLjHhBjJgCkT53tPw3LgAc9f3tU9ZWQhVNknN1KdN5ayv4//a4POCrjtdVNMp8i7ekc3VjLPX3jbHkCItsLwIyhgu1WXq0punP6ezKCNMdNRUJVGsw8isFiFcjxzefPjKuhE1xNuSrKIG37k+vTLbQexA5kx8d+UAg2xNsiEAKoW0FgI1wb9Xa04GKJMhcX1rUdePZC6fBsgGwkwFOsGEf8rv8hyNHe5EzvAb9oaLAw5Wh3VPWvskESrG5uEb1niIgB5EzY7hQAUCUsqEqDIFAGCwCsv7k4CmBFuNsOIAqGdpe6uiIuvD0QJNl9y7O3pJkzllVmRI7OhUxWwsA96LfMgB4GKEiIAq6DwCqjVW8oZTuWHYQ/ZKHZ/vSndel2LiBitZFPlZxNjVK7JpPRmNPTkpYJgApNpfhE3MPNXVjqX1VQhjbfFWNsWFV2Xzn+wNnWizLM8GWHx18zRfZEyXmCHGbknmoCEipoyOqoB0BRJNsKCT8Nn/i7bFWme/CocAI9zggqooVBjbvveDr6TXoDyWcvC/F5hIm0JTYQxgtPzuTZaeR2tR5um/R8ovESFmBeJSYJ23Y4TBf2cbm2WqD+Y7gS3Z5eWQkLDZnvKUJ9qSzf00w3+AQYULCXUmHtvghjiuQcIjZD8M17snywZksOy1ICBAPLq89OfCeVexMsuEQqob4RSi21EQkAiKCbMiPjPhNadWklIY6IrUbrKpfFxFS3FkNdT+IUjFmrovtawQ/m2XP0oQuPCkA/Jnvb5+09jgBGmFaDsLSCBFX1b7qVsp/ma3xm9uS8ydGRgLIE3FmVsKCKJlvqqoNRKuOo/e3RO/MmGcl0JjdHP/w9JFxUd0UI+ZAJSAgDFUDY7kHzc0421rJOwi3TIodi4BRE1tPsTE+7KPrjpePtUTvvAkAQBH9YQmuyZ8Yfn7chm/EiCNJNk5d5Zn1JweHS3B5ri1JgK5GjtdV3jktQG+cmaNEzoTYip9MPdwSvbPPfdGbTZn5ZwzClNixBGKFAsCzSzjT1qDflgCzsHL+0+PWHjrXRIwV3Xj70Tenxc18Zz9nJcAAQCnd9YqX6drc+PZ/PE6XdK/vy2TfAmZs1Tnsv2MRuI3E79x/AAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAADAAAAAtCAYAAADoSujCAAAO00lEQVR4nK2af3Bc1XXHv+fc9/aXJIdfRlqJ1rRxg1n9cKiTlqbNKJkEQqExMeYtacmkhZDQ8KNtYhtj40RawIB/QOnQJCTTDJO0GLNrk0AY0k6mQxVIaZMqxPpFSBjAri2hGOyCtL/fPad/vH2r1Uq2BcmZ2X+kp/3ec+69n/s99wlYYmThGQDY19n38cc7+3721MqVyxSgAYCX+h2LRfj32c6e72c7e+9p1FpKLFWcPKQ0m0pFRCVzmnFXz87GNhOgQP87TiALz2QA2dfVc2kbO5cY4OZHlne/20NOllqYJT00gH5DyAiO8/VtxrngqF+uOswbHuvse88ghqy+s1kgDzl9auXKqCjdW1LRKHHCOLSDAO2GR7+RBIJKDMkTyTVnMdGXiyIqBMSYo1XoDgI0t0SxxsjCYwJkthj/fBubVVUVKanYKJv1ua7uD6WRs0tZSqdMoBseZQApUWWghc1ZPkQI5BbE2jjxJ3KdvR9ZqlgYAwB7yMnjZ/e0G8GXSiICEAtABECVd2fhGQ8p/bUSUIDTyNn97X09LnB9XqwQiAFAoAQAqro7GHwOAJY4E/1MgJYNBhLGnFGFCAWD55Ja28pmDZIv/hUhI6cqzEkTCJeGNbIrQuwqNKyIECgQM8570fHCdWnAZuGdckazgMlgyN/f1b06QnRdrSgGgCigBKKyiBLpHd9Zsfq0ceRUT1KYEwpm4Zk0cjbX2f1ncTaXFNRaBRkCKELMdTEVZabMk7/de/o4cnpqengAAF94t8vGVagqAJeIHSJSgKsQaWWTrFTtlgwguZMU5kS/IA8pHUulIgDvVIUCUBekopguq+yIBmJUVZEWNu2FKrZlADkZPepFSfZe3sL80YL4VgG0kqGq6sO+yjMJMkoACmIlAr55X9f5v+chJyci3aI/DLE5cdzc0Mrm/JJaUYDizATSr6UnR2+tiB6IExMBWhArDtGN+5I9qwKxgcW+t45Nhe60CIrigKgEm9dY/kaobAWUAIKFSpQ5LsonJd0CoWC9Dcn+9r6zGdhWEhECNELMM2IPoRi9XwEi5i8oAAJRTSxqgZ2B2MQCsTo2C7Gb2ozznnK9KIarqjvSL7/8Znpq4tm8yndbyBgAKIhvY+SsOxnpFiSQg8cZQCzLYIsxZ1YhCoBcYlLFlvTx4TdzqZR75ZEDT5fE7k9wIFYUa+NsPr4/2XNRs1iIzezyVIdRvq2xKLPiv1K2fF+4RFzQ5rJKyYBYg1mCqu56Gv2Oh9wCrM5LIAuYNHJ2b1f36gjxdXmxAkBjZEze2v/0pkYfycIzmJiwChCMe2tJbDEQCwhlQbsbxAgIzhICFI7JJAyfPlcUIoVu+fT0SH4Q/ZyFZ66YHPlFVeWBBBtWgEpqbZtxLjiaPHYtAQuw2jQDASFYeZdL5ApUCMQWqiDdSLWKpAGbg8fpw8+/5EP+Yb6Y6TvaeexzNTGeo1nfBRHGtWFREuSYvNhn05Pjj4ZoDSlWdenuWbFTLjEToCURZUKddI1YrScQCmU7e9cmmC8qqLUA0MqGS6oPpyfHngufCVKtGa5i7J68tUdCsbKKEHTgsc5VZ47XpjybSkWgstshdgQqDOIqpGBYv9hYuJBiVx8aPa6KL0eJA9IFWO0oVLG1GasMBBt3HCl9aMWKGKnutBoSgrkgdobV3hY8M7cGQ8OVPj78ppB+KRSrqGgrO2dX4WzLAJJGziKPM5TwwZKIUrCfuKR6aP3h8Z8M1E778HvTyAXmcGrVQ7Pi/zROxhCgebHiEt20L9mzKo2ceICpJzCIfpNBRtqqbTe0Gee8OUIw+9Bd6amJQ+HmblxwwWaF8SbHvjVr7U8axSLgG3Lt53cDoPTBidcI9GALG1IAFbW2lXhVrqP3LzOLrOscPEojZ5nMxqBiAekizDEf2AFAvXC5DwA8iCGbXZHqAGhboYEQefFfOc3yfSFFsGh4IEBYsVEQYFUCsYhl3hFMMCiqkdvzYl93wQRAfShAeudTZ/zBsuZ1HVLsyiMHni6KfaylTjrftrBZm0t2Xxw+wyEhpMy3t7A53W/ApoC3fmx6JB+ar8WGXxd7bfSHRZFsDatUEGtb2Lks29F3CQBdOzX8uq96e4yZa0vNthrnnJloYdNidsGrJUXG3TxHutCMhaRLKQcbN/XemOE6IeJkTF78Z9KTI3uDrmnILhz6XIQVtC62lNTmDYgQeBwFye7/wRp3AOCVndEHZ60/FiPD9ROc6YuPt/f9TrNdoFpS6cPPv+QLmkjn9B7teOMzhIzU/sDscEEmJIQPVVjZGK5INFVfAXoa/U74+RD6eRhrnKsPjb5sRf++QUzajNP9crJ8fQaQ9w0PV6F0CwEEkFhoJUocKxq5czG7UCddInLPrPiTEWJDgBZVlIgGHutcdSZlk71/EWF+uKoigGqCHDMr9qGrpkavbcTmUiILmMiZ5yWqEXfCZe6qqliHmK3qsYhGzh+eGj6WASSb7H6y3YldNqs+AEKMGG/Y6uXpydEnmjXn8N5zTZzMNwNfRpQg5qLKNxxAN3LNWCmIKyqiQvcpwLmg+vUYwABnkJFsRyoVM+4nS2p9BhAlw0WR76WnRn+KN16cyXb03RYh/lZFhaoq2sbOWW9KZSAD3DwAcAx6w/+J/7cVtaxEQlCHidyg6vO7sHQ4C5Nj3y4me+6IsOmqtZ9CwOcol+z9bJTpGyVVIagm2DGzvn3gqtdG/6a5GgMAZwDJnpNa6YrzyxY28KFhBQ9GNZIanhouDQKa6+x5Lk7mD0tqfQKxA/KtxZorpkfGTwSExWc1GMO+zu6rouTsKauoAJQg5oLaPQQA2c6eHybIfLCg1meQcYikCvl978jYiAJMDfwPv3BvsvfvWg3fl7e2AgDLjBOdEbstPTm6HQD2JVf/scP6jK8qAtUWdpy8+P+anhz706fR7zQP9D8wJM3njAI0CNC72/vicSNjETLnVtT6DthY6BvkVPsYAAzRLT5UCcQCtS7YqNJuYK6tbJ7SYuSMB4tWfhljE2GCKYoVA9yyv6v3HAXoyqkDPyqp7J3Dqm/jZC7Z39Vz2Ycx5B/F2fphDPnhp3nwNW3OAOIa2dDKzrlltTbsSwR69/pDP5/iLDyz/sjof5VEH25hwwCooNYmiC/KdvauXcSHazc8uubgUMlCNzvBAUQ+VONslvmi2yngNUWt2VoUm3fCywZARWlnNpWKeKfodcPD8zud3b8VAW0sihUCNEqGZ6z/i9ZE6SsKMIcMj5JsLYrMOjWGW0ChuvOplSuj4w3WuDYLNgvPfHJq7LuzYv89QY4hAHmxEmP+1P6u3gsJ0HXTB171FbsbsdrKJsXHzefpFL1ueMBWle+Ks2nzoQKADEBKtOnSl14q5+ARh6fgusnx//UhO+M1sXLA8PNmC7GbMoAMoH/R6w0m3lRVqQDEgIohYqu6awD9jgIUrVTunRF7KDJnjYUI2/a39519okuAcJ/t7+q9MMJ0dUGsJQAJdkxB5QeNuGVg7sAoW75vVuyrkdpJWRIRo7w1uzzVMYgh2ygWzoI3OfJ8RWVPCxsCgs6slZ0/6e44/lEC9BNvvDijqlsjc9ZYE2zOqrIMnuISgKzovSZoepRAXBVbJdKN8woIzFnjT0+P5BW0xSUihGKGz4BrMs33laG9zi5PtQL0/krgUsiAqSj2GBQvhtX0psb2zFr7XKLBrUaJPpvr6ulr3mP1gyvZ++ctxvlAUYKN28KGK6r/5B0ZG2nE+2IVfTQv/rNxnufDP5Pr7LugUSwkhLr8hXcZp7sSEAKBBZe709Mjr4TPEqAg3WCDDo8UKi4bR5R2Beq5BUUhwl1VFaXgOofyYo8Z4cHmvmTB+iNAyfJGX1VQ63UjYKMqu8JnPMCEhHBAm/INhJi1/osFd/YfQ4qESacnx54ri+5JsDEKUFF820J8cUA62Cw8Uy+KYza2sllRUREFKMaGhXDH+umRXzX3JfMSqM/C9Mh/l0T+uTXY0CiobxNsPpJN9qxLI2e9VMoE7SNtDwgRWHATmLTN1xw8WKo38phzq6z+tqLITIjV2t3QjqdWrox6SKmHnDzSvvpcl2jDPGyKfQHvsl+t3dXOOy8WzEAo5jJtK4h9y6k1IDaw4juyy1Ot4xMTfraz549izJ8qiLWoNekFlR94U6OPN1uQkHRXvvbCQQvZPY90bFbNzsZuJGSEACVj74oxtzZi01jdlJ6YqNQO1Xk2ZFEChAN4NNm7dZkx22fEtwCwjB3zlrWb01OjO3PJnudibC4sBvaDDWCF5P3rj4wfaLYf4foeBOgD7X3xt4yOuUQrqirWCa7V33I10lXWcipqzI8rKgvsR3DlgwXOeNGDJGwu4ojcP2Pty9EQqyoK6M25ZPfWCJsLixosnRY2XFb55voj4wey8Ezz4GuV0m549LE66QKs+lBNsDmthMq9yrTdEEihYBBXxFZdplvCUS0WiyYQNhdrp4YLSrrFCbGqSkx0jstmeyUkBDHlrT0Wt7SAEM0RXgKkJ0f25sU+m6hdIc6IVZfprx3QxXkRKIAWNlwFvr7u8OjoyfqSEx7lwfXGAF81OZbNWzsUZ2MAtQpoVYP1qVCNEbNl3Hn5r8amg9uNhdWfH0ElHcsb/ACrIABVVQ0MJcQlprzY1yHm9gGAT1aUU7zgyBAAuMAGX9VSQA8AYIVKjRA/b4sXvxr0CifvncPCZOGZK6YP/Lgs8i8BVjV8Q1Mviqjenn7tZ0fDV1zvKIGQz+umRofLYr/dwoYVagGAQGoAIgTGqnsRQpwoQtI5TLcVxc44IKrdOEiMjJkRf/x3p6IPLobNt5VAo5h1ItsKYt90gsPNT5AxBbX/5k2OPPl2e+cQq+uPjB62IjvibBhQS8HLAZDQpvdhuLoYNpvjhH68Meb8Sc+ty4xz94z1qw4xg/WCdYdHRxfD5qkixOqa5JpYmcpjhvjcSGAZvn/V1NilSy3Kkl5Qp2tYXR6Zuf8t8cfPdCJuBfJASIi3O3hgDqtrp4YLRLwpRkwVlZKrugFLftv5NqJu4jp70t/rWv3qE8k1Z+lv8n8lkr0/yia7v9aotZT4f+lZ4aLbbAH+AAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAAEAAAAA8CAYAAADWibxkAAAVxElEQVR4nK1be5CcVZX/nXO/fk3PAEkEZiZAVpddoHsmBIe11K3dgLuKLxAM3QR2fUQFZH27kJCA9LQ8E9BdLVSiq+juakF3EgQfxaorTBVaFDJKMjMd1KgEJzMTAgSY6e7p7u+es39839ePybzZUzU1Nd33u/d+555zfuf8zh1gmaIAKUDfX3VGxwPda391f1fvNgDIAWa5cy4kwdy5kxNv+Mkp6/bmVyfP8z5PLXtNXu6DeaSYAJ0Oh6/rYHNuiGjrnlOTf5kCJPMq5p1fUp7i2WwPEa9VpS882dcXSiGvAGg5My5rowpwCnnJnZI4PQRcd1RcN8LcXnNpOwGaRGpZm5lPckiZNPI239V7eYcx5z3nVisd7Lz+D+PlqwiQHFLLepdlPZRHighQWLo1ZkxUoSiLtVHmDbtWn31+Gnn7asxypihAI8jr91e9uYNJb6mqKBOZiooYmM/lOtedOIK8LsfylvxA/SRWJ8+LsJMuiWsJ5CgUDIKo7HgE652RV2GWM6Uf600WkGrklc/E2XltVUUAOFUVibM5WeFuywKyHMtb6gOkAOUBQlfy8Rg7506rtQAZAFCobWfHvGLtlRvHh/4jUNZSN9UsGYD7Ad3dedZpzM4+AO0uQOTtRQ2gDKq6hs5N/XlvIYUU55ew5pIsIOcFPkFX8oPtHGp5eQAgEFVU1AH6H1hz9gkjyKu+SitI+u5m2bk1yuY4F6rkz0kACVTDzFG1sh2AppY4/6IVEPjhA2vOPoHAn69A1DuI1vlqKjZuzOpa1W7LApJfZnACPNhLI2/z3ck3h4muKIoValK4J2RKYm2M+F27Vve8c6nxZ9GbyyPFWUCqNbu13ZjumueHDAAK6IwNSYjo43u61/51Cvllw2IK0AzAqnynAyJA6+s0r6kACQBR7MghER5BYtHxZ1Eby/iwt2v1ur8KgT5R8k6i/iw3LRaYZYRNzIUuGxZzSBkCpGd1zxVxY948w93UX1P9Nbmi1nawk9Qu/mgW2UXD4qIGJT1bV9Ha9giZmHgnQQDUP5YKAGk84ZllhPji3avXvmWpZtkMe1C6taYNd/MXJlWUqUXxRNMi4hDdmOtcd+JiLW/BAV4kh82tXvuWCDmXlFujvsbIkEDfr9AfxNkAUOt/BwJgRe7aib5QaglmWYe98OS17WxO82GPvahPKsCkJVwk0HGHSH134BpE2ticCLY3LdbyFlIApZDQR7Degcqd5L+YJ2rbyHBR3ccuGxvOgc1tFRFpBEYyFbW23TjnrFhd3USLNEsP9gZs7uS1rzWk/1pucTe1bcxEql/eODb0Mwa+ECUmggoAUBB/QFflTunt9Sxv/tpk3g15sJeV57te3NTOzuub/FABopqqGNbPZgBOj+59oga5t50Nq28F8GGRFdncKYmVi4HFAPbEyK0xduK24W4SJjZTYp+drrTtyGC9Mxla9ZUpawthMkY9F/RgkTisVrd7M84PjHMqIAPwCPL63dN6V4CQraoI1f1QJc6Gq6rf2TA68qvzsJ4VILDJFMW+4oCpbpYqNs6mU61ZEBab8v2/ixJvLIlr0Th9DRETRG9434tPvJJMHOFNBwemIbKFAVAdIcgU1ZU2Nu/IdyffvVD8mXMzSaQoC4hTkxva2XRVVdQfLw6IymJfchg3KUCPYkD6sd6kR/ceUsVtMWaGb5ZNsPixXV09Z6aRF519XQoUr9AdBkRa/1xtlIwpWvfx4Ynh7+UAkyoUajmkTOrwyA/Lav+njRyDhuX5EZm337tmTXQ+y5tVAYoMp5GXXV09Z4aJ/6UkVoKTUKjG2LBV3bHh0NBoELD6MWAzAKuJf3nSugcixKyAEEAWqlHmqBDuAKD5WYJTDilOI28T3b3vixvnjeUZ7mahIjDXZgEBUiBA/TIYYLquorbmW6gGsNjOJhF3j/uot7/1s1rBrArIo0AAVIhuD7OJWT/9JMBGiHnK2gNRRL4UBKzgmJJIUXr08TKYthoQBWZJIFMUa6Ns3rOra+0FM80ygL3cir7jWXFzTUVnutu0yv0bx/f+orm+8Mtgkx4dGqqqfj3eFH8IRGUVJcGND57UczIwMCssHvNBPf3sSr4tQnRxWaylBuzBgEkVWy8aHywFASt4No28VWQ4fWhoV0nsz2NsmszSe1ML3bETfaHmajHIMiVW2dxuzKmtsMc8LTIVsbVtgaKa9xuYN6uTLYo94oBNEH9cFWk3ZlXFIDNXtXiMAkaQ8WAPvJ08X/IfUhsjY4riPpKeGNo1d6WX9X/T5pqIS416wVTU2g5j1q7orl6Z9UmMDMBp5O2urp4zQ6BPNcMeQSVKTDXIjksO//aZwN1mrCZ5pDg98dQRS3pLlFtgkYte/PnI7hOTZ3sH1PrOLX/kkDJZZOW57hc+Ejdm3bRaS94YJRC5qpYVm73R+WPfHU1mOT40WBO91zdL1/+WplWUgc/t6T5z1QjymkwkHAAQxbva2YnXvM37vkymJLbiEN3ru5vMtmYaeckgw3S83DNl3eEwGQMfFtWDxZAN8Z3erlutoK6AwLz2dJ+5yoD6KyLaFPikjQ2XIfemJoafDLLDWTWAhlm6oVB/ydoXHTA3m2WcTWdNnBuzgBwtxDyTVt4zKe5LocZYUqhtMybiqn7CP+m5cghNokDpQqHKCh8WA9ckU1JrY8RvzXX3XjQz/tQVEJiXq6FtcTYn1yASkA4OMZXEvmTY9M/mhzMlMMsr/vybMZfk9mZYrJsl8zW5zkTiagzWdvb1hdKH9/3JqtzZPFZBPC1WQ0qfzHX3npGeJ78PXuzSieEfl0V+FGNjtAUWVQFsf2TN+hZYDMpZzmLA7ulMnuUQfazYBHvw/JAFuC09uvfQbH44mwTFyEmh19w9ad3fRchwc7YWYY6A+A4AGBts1wzAZDr+bUrsgbA/1odQiRgTVXgQupj8nsTdUhWpsg+LAExFRTrYnHmk9sLHmhMyBup+oS7R9ghzRHzYU0AiZMyUtb8Hx+9uhr0FN+Fv9vyDA9MCbDGEFlgsiWujbC7c3bn27VkMuMlEwkmPPl4GYVuodSyXxNoo8cX57t5/mC+zC75LHd4/UhG9ZyYsTosVBt3wwzWJzuCAuJ5+dva+I8rmwqK4ddaFoGo8NN6SHn28PBP2FpIAFjeOD3+/JPLT2IxsDQCEZEcukQijUPA2f2goP2Xl0aaxBL+yVMVdO/taIXSmBOZtKXxzUdznQsQMz/K4BtU2NitKNZMNDohTyGsOibBSUDx48yrUxsgxJSs/S48PP5DxssMlE5x+UgWAN9easjUAZtqrFntxlK9KAxaJEQMArHqdC7GMgAIgf6xZt2KiuimA0NnWC8z7n8YHn3ehWS8jbbEmCRM+nO9ee04aeeuRnN3mIx3G6W2CPTCIqmpdl9zNAJBEdiHfI53lJzDL9Njep2oq3zzGLFWECTflOtedOFIouDv7+kKpieEnqyrfbgpk6hOuworsd0/rXeHXFLPuKQiWL49FvzFl7b6oB4sWPiyGyBiF3A4AnOtcdyIpbpxuTT9tOztchd57xdj+3ywEe74ozfIDNMFilPqL1r7QBHVcU9E4Oycq288FwVUBCldqN02LfTlM7MCLyFxVpXY2naGqZuDVFHMVc5pEiq7GYI1BmwOCwv9lSmIlQnxBrnvtZZTv7t0ZI76qrGIBGAU05MHGmBVzTmriqef9p+f1/Ye6+tpiMYRGrUsnAHgJwAkALjm49yUAeATrnfMx4Oa6ez7bwc4XprxS18Dj95RAtarFuRsP7xt+sq8vdO7gYO3+rp5PtrG5Y1qtQon9sSRAkUO13g3PPj2eAXguVAriW66r9/425nRQYCmgDgiu6gQrtP2YJwkkgNteDVf6FyAwgtSywtVvVyv2mXhVDrhV+6f2qvyBa/rM/d29/w0ARzCgCjBOkLsnxe4PN8GihWqEKEIsOwDgj4ODogBdNj78ZVfdM1yja8MhSoRDlNQQkqRu38pnTz4CeD4/9+6CbFVLRFQ/wSAIERClXFfiNIbzG2ascFU9whFqj/M6PHelx4eum6/DE3y3q6vn4phxHpgWCwoCKSnayGDK2otT40MPfun00yOfOnCgkuvuvaiN+MFyC9Pr1fxl2HenDw3/yJ9XsATUmX1fyb8NsfNYTaXOQyggUWKeFvdKAoBcV+/m44zZ/orX52uYpVLVJV2XHhv6nU/LzqptBZgAub+r96G4MReWxHUBMgS1EWKeFvk9TPs5GD21CnjweH9nz8PtjnNBqeEKNkLMFZX98bby699x4EANgPbPYYELJGOkyFA/skh29T4WY35Tg85TG2NjymIf07GzzuMMwEcRvnvS2j9EZpolc1RFt2MOEiOQvLdJCqlsqYpUDYi9weRMq2iHcc6AFD+eRr4BdQabqyLVGbAocTaJqWLko+TDWRaQ2X7mefk6l5nsTP5z3Jg3tXKZIKsKUnN9GnnLwHq+enywZBlbDbWSGCV1bcyY9+S6et46fwYGm0OK3zsxst8V+VqbB3VB7u9lYIrrv3fqGd2pQqG2s68vlDo0vK+i8o34jLEVFWHw53a/bu1Jy+kqBbXKf51++nFEfEtrT0Elzg5Pi9x3qU+ucBYDXrZ2aChfsu7PY+Q0FRGB0I7gJsZc2Bv058vs3jwl7nMhMKEpA4sbs5JtqD9AEwUoFOPPF8U+3zJWReLGrLJlWTS33yyB1YSL0etmkis+l1kUMVsVoBTyygC0ka3R5hrEmlkysD+N1z5MmJvVDRiXD4w9/YKCslE+JgOzEdCm3af1vv7qwcFaPpEIbfjjvudU9eYZY41PYly1e7VHYqQWee8og4zXwjup93Vh4k+XZhR1MTbsKu66/PDeZ/wrPsJAo4hIjw8NVvwMDE3ZWkVFCZrZ0/03q+a7iREwvke7wt+YtHZvrCkDEyjCxI6t6V0AgELS5pAyZ62QeybFLUS8nL2RrbEJucrbgYWY/YYkUfB7CnpbhLk94DLh9RR4SuzBk1x7V8bvdQJNLxJka+01ubEsctRpzdYkzqbTRfmGubg1XzSPFF09OFiD2uu1XswAATHRxub83at7Lw0CYk+hUAVhM4NIofWxZXFtG/EFHomBBXuLAZd5X2fv34fZpEtNXCagGgKTiN54/pHCFLCeA1esKyAoIt59pDDhqm6PtnL7XBIrDuiaPZ3Js9LIy1wtp4B3S08UHi5Z+WWMmJuJUQLEKj4NAHm/AkwfGv5RUe3DcTL1sQoi8TZ5x71r1kQX6i2mkNEMwIb0TuPVIYF4PQV1f1GYGP6eIsNZDLjBly2mHJhwKfzKl5pJDKqTGCbqEm0HoHMZpu8emutMJMJMvdPaTK0BRMSk+LG36RSCbC1k+bqqah0WfW5fOticFa91fGy+3mIG6x1CVpLdve9vM84bvASrfkBkVUVUt3iH3FrUzZxQ80jRpoMHp63qVoMGMYEmEiPXmXj7XLBY5wyI74iyOUF8ktMnV7ho7e9eE175RT8KS2DeGw7vG65CdzbDIkA8LSJGadv3Tuo5eTZY9FBpQHIr+o4n1Vuq0lLUuXE2XIV8d+P4yC9mK+qO0WhgwpdPjOwpifuz2UgMIt6RS3g3MZphsdHbS74tyubCqaYrLT65QlZl2/kHB6aDi5ZAA0JJzM1TYp9zfBKDAKpBtM2YlcZQdjZYDGAPker17cZZXUMz7DGXxU6GbO2mubjMOTpD3ksJeEtVrTuTxIgbp1dfMldmka3DYoCruUQirPAo6EYFqTbGjila+78bJ0Z2z6wtvMxuPacnnjqiqjdHiakZQotiJcL4UK777HXNLW+tX9g853TD+NQM2LMxZrbQuy45/Ntn6opajAICs9w4PvTrmuKbx2RrImLgkxj+6QW4ipecD3cYs3a64YdeT0HEVdDm2dYDgH4MeHi/Qr4+Zd0hj8TQOiw6MCFVuRPwAh4AalzYrN0ea2rhKSBhMmZK3D+hJl9shr1FKQBomKUTqmWLYo+2ZnaicTYngd0bsoCch/WcQl4e6up7DUP6K9LaSm9jw1XotzaOD/16rsqSAE0hhXShUIXy5sbHAECmrK6NG/7HXV09FxOykkskQmnk7a7VZ58fYXNpqVHIgaAaIiKFbksfKUzNx2XOqYDALDc8+/S4QG+ZmdkVxYoDvua+k9f2PIoBIUDLVLkhzs5JVXit9KCnULZy1Di1BXsK9YRsYt/DZbE/bGOnBRateg3b3ClvjB0txHQn+kJWZAej+e3URskxRXEfS4+N3KcLcJnzFhr9GLAKcDG06quT1v1tc7bmc/thZrkzC8juU9b2hMDXFFuvtEiUmF3CbRuefXp8Lj9slTwAUEh1c6WJ22+CxTNhpz59NQZrK1ZXPtRhzLmtvAKRhVgivtabrUBzLoUF2B6gEdl3d/deFCZ+cMZ1tYBYuECJPtjBzuVNVJeEiaki9nfF8OS6Zw4erGa9g1qQ4AjWvK+r54vHG+czk755exZFsKJHFXgbSHeFidfUVH2L867qTlr3Py8bH/7AYq7qLlhqBma5YWzoobLKT9sawQkKUFVFlTgPIFUUq00MjxovJF+/6eDBaR++FsXuBHgfJvfWosjhsB9/CCBXlYiwkoCfEOgvaqqEBuzRtNiXO5huWEwLb1EK8CTg1uzmqkot4Ou91hXIgI7zbow3Wult5JiSyk8vHR/+fpCnL26tAD7X83vHnn5BgGy4Jf4AAqghWokWharEmNkV3PnOQ0Oji3O3RbhAIA2GteerHca5ZrIp6qqvjGAnBKgBbA32DemxwlMBZbbYtYK9ZQDqRp9Z2VV5IsJmXcVnrmdZU0LEVFP543GWzv7l4UvKWWQX5W6LZlvq3L5Ftij2+RDVYRFNG6lfaakovpkeKzwVXHldypsHUwXcPqBb/HXqQi1/qoa8y6zbLji8r5j0r/gsZpFFKyDrXzS64rnhw6K4NdrUcmoSCRFT0doX2LXZjH/VbrFrzJR6s3N85CdltQ+2trwD8UjOothHU2PDuaX+j8KS+LYAFtvj5a9NiS1EGxcUva1ANUrMwrglfaQwEVy1W8oax4oHiyK6tSoy7V+fC5SqDMAVtcbInFnmfLIkBZBfLb7zwIGKC9pChKYLirARMmZS7P6i88o9Ok/6uRQJCNfLJ0b2V1W/0sbNnIFKjB0zrfY7G0ZHfrWc/1BZ8j3+wCwvH9v3w7LYh9s4IFHVu0UJ2rLp4MHp/BJb6fNJvVo09rai2PGwn5A5ICqJfZmNM2e1t5As8785PFg0zNdVxdYI0DY2pqT2JxvH9v1gofRzqRLQcOnRwoui6I8Qsyhcj+TU7enRvYcWC3szZVkKqJMYo/uGq6JfOY5DTsXqNDFdW9/y/7MEbNXI+MpvTYr7xErHiUyJ3c+m/d8z3lW7Zbnbsv+fJ4DFEMduKat9xiXZmR4dGsoBy4W9hUTzSFEWAy6It0J1zLJuC26uYJnu9n92cieJtdEdOwAAAABJRU5ErkJggg=="""
_PNG_B64 = """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"""


# ── config ──────────────────────────────────────────────────────────────────
_CONFIG_PATH = r'C:\CirqueRMM\tray_config.json'

# Local spool for tickets that couldn't be sent (offline / server unreachable).
# Lives in the same dir the agent already uses for tray_config.json so we don't
# invent a new state location.  JSON-lines: one ticket payload (wrapped with a
# little metadata) per line, appended on failure and rewritten on a successful
# flush.
_SPOOL_PATH = r'C:\CirqueRMM\ticket_spool.jsonl'
_SPOOL_MAX_ENTRIES = 200          # hard cap so a long outage can't grow forever
_SPOOL_MAX_AGE_DAYS = 14          # drop tickets older than this on flush
_spool_lock = threading.Lock()    # serialise read/append/rewrite of the spool


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


_LAN_TRACKER = 'https://tracker.corp.cirque.com'
_LAN_RMM     = 'rmm.corp.cirque.com'


def _resolve_tracker_url(config: dict) -> str:
    """Return the best available tracker URL.

    Probes the LAN hostname on each call so laptops that move between the
    office (or VPN) and external networks always hit the right endpoint,
    regardless of what URL was in the config when the tray process started.
    """
    import socket as _sock, ssl as _ssl
    try:
        ctx = _ssl.create_default_context()
        with _sock.create_connection((_LAN_RMM, 443), timeout=2.0) as raw:
            with ctx.wrap_socket(raw, server_hostname=_LAN_RMM):
                return _LAN_TRACKER
    except Exception:
        pass
    # LAN unreachable — fall back to whatever is stored in the config
    return config.get('tracker_url', '').rstrip('/') or _LAN_TRACKER


# ── helpers ─────────────────────────────────────────────────────────────────

def _get_icon_image():
    """Return a PIL Image for pystray."""
    from PIL import Image
    data = base64.b64decode(_PNG_B64)
    return Image.open(io.BytesIO(data)).convert('RGBA')


def _save_ico(path: str) -> None:
    """Write the embedded ICO to disk (used for the tkinter window icon)."""
    with open(path, 'wb') as f:
        f.write(base64.b64decode(_ICO_B64))


def _get_username() -> str:
    try:
        import ctypes
        GetUserNameEx = ctypes.windll.secur32.GetUserNameExW
        buf = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(256)
        if GetUserNameEx(3, buf, ctypes.byref(size)):  # 3 = NameDisplay ("First Last")
            full = buf.value.strip()
            if full:
                return full
        # Fallback: NameSamCompatible (DOMAIN\user)
        size.value = 256
        if GetUserNameEx(2, buf, ctypes.byref(size)):
            full = buf.value.strip()
            if full:
                return full
    except Exception:
        pass
    # Final fallback: environment variables
    domain = os.environ.get('USERDOMAIN', '')
    user   = os.environ.get('USERNAME', '')
    if domain and user and domain.upper() != os.environ.get('COMPUTERNAME', '').upper():
        return f'{domain}\\{user}'
    return user


def _get_hostname() -> str:
    return socket.gethostname()


def _pending_reboot() -> bool:
    """Return True if Windows has a pending reboot."""
    try:
        import winreg
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'),
            (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager'),
        ]
        for hive, path in keys[:2]:
            try:
                winreg.OpenKey(hive, path)
                return True
            except FileNotFoundError:
                pass
        # PendingFileRenameOperations
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager')
        val, _ = winreg.QueryValueEx(k, 'PendingFileRenameOperations')
        if val:
            return True
    except Exception:
        pass
    return False


def _pending_updates() -> int:
    """Return count of pending Windows updates (best-effort, may return 0)."""
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             '(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search("IsInstalled=0 and Type=''Software''").Updates.Count'],
            capture_output=True, text=True, timeout=20
        )
        return int(r.stdout.strip())
    except Exception:
        return 0


def _toast(title: str, message: str) -> None:
    """Show a Windows toast notification via PowerShell."""
    try:
        ps = (
            '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;' +
            '$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);' +
            f'$template.GetElementsByTagName("text").Item(0).AppendChild($template.CreateTextNode("{title}")) | Out-Null;' +
            f'$template.GetElementsByTagName("text").Item(1).AppendChild($template.CreateTextNode("{message}")) | Out-Null;' +
            '$toast = [Windows.UI.Notifications.ToastNotification]::new($template);' +
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Cirque IT").Show($toast);'
        )
        subprocess.run(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ── ticket transport + offline spool ─────────────────────────────────────────

def _ssl_ctx():
    """Match the agent's existing TLS posture (CERT_NONE — do not change)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post_ticket(tracker_url: str, api_key: str, payload: dict, timeout: int = 15) -> int:
    """POST one ticket payload. Returns the new ticket id on a 2xx.

    Raises on any transport error or non-2xx so callers can decide to spool.
    """
    req = urllib.request.Request(
        f'{tracker_url}/api/support-tickets',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {api_key}'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        body = json.loads(resp.read())
    return body.get('ticket_id') or body.get('id', '')


def _spool_ticket(payload: dict) -> None:
    """Append a ticket payload to the local spool so it isn't lost.

    Wrapped with a 'queued_at' epoch + a random 'spool_id' for age-capping and
    de-dup safety. Enforces the entry cap by dropping the oldest lines.
    """
    import time as _t
    entry = {
        'spool_id': base64.b16encode(os.urandom(8)).decode('ascii'),
        'queued_at': _t.time(),
        'payload': payload,
    }
    with _spool_lock:
        try:
            os.makedirs(os.path.dirname(_SPOOL_PATH), exist_ok=True)
            existing = _read_spool_locked()
            existing.append(entry)
            # Cap: keep only the most recent _SPOOL_MAX_ENTRIES
            if len(existing) > _SPOOL_MAX_ENTRIES:
                existing = existing[-_SPOOL_MAX_ENTRIES:]
            _write_spool_locked(existing)
        except Exception as e:
            _log(f'spool write failed: {e}')


def _read_spool_locked() -> list:
    """Read all valid spool entries. Tolerates a missing file and skips any
    corrupt / unparseable lines instead of crashing. Caller holds _spool_lock."""
    entries = []
    if not os.path.isfile(_SPOOL_PATH):
        return entries
    try:
        with open(_SPOOL_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and isinstance(obj.get('payload'), dict):
                        entries.append(obj)
                except Exception:
                    # Corrupt line — skip it, keep going.
                    continue
    except Exception as e:
        _log(f'spool read failed: {e}')
    return entries


def _write_spool_locked(entries: list) -> None:
    """Atomically rewrite the spool from a list of entries. Caller holds lock."""
    tmp = _SPOOL_PATH + '.tmp'
    if not entries:
        # Nothing left — remove the spool file entirely.
        for p in (tmp, _SPOOL_PATH):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        return
    with open(tmp, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    os.replace(tmp, _SPOOL_PATH)


def _flush_spool(tracker_url: str, api_key: str) -> int:
    """Try to send every queued ticket. Returns the count successfully sent.

    Idempotency: an entry is dropped only after a confirmed 2xx. Entries older
    than _SPOOL_MAX_AGE_DAYS are discarded without sending. On a transport
    failure we stop (likely still offline) and keep the remaining entries.
    """
    import time as _t
    if not tracker_url or not api_key:
        return 0
    with _spool_lock:
        entries = _read_spool_locked()
        if not entries:
            return 0
        cutoff = _t.time() - (_SPOOL_MAX_AGE_DAYS * 86400)
        remaining = []
        sent = 0
        stop = False
        for e in entries:
            if stop:
                remaining.append(e)
                continue
            if e.get('queued_at', 0) < cutoff:
                _log(f"dropping stale spooled ticket {e.get('spool_id')}")
                continue  # too old — drop silently
            try:
                tid = _post_ticket(tracker_url, api_key, e['payload'])
                sent += 1
                _log(f"flushed spooled ticket {e.get('spool_id')} -> #{tid}")
            except Exception as ex:
                # Still unreachable (or rejected) — keep this and the rest,
                # we'll try again on the next timer tick.
                _log(f"spool flush stopped at {e.get('spool_id')}: {ex}")
                remaining.append(e)
                stop = True
        _write_spool_locked(remaining)
        return sent


def _flush_spool_loop() -> None:
    """Background thread (started from the persistent tray process): every few
    minutes, resolve the current best URL/key from config and flush the spool."""
    import time as _t
    INTERVAL = 180  # 3 minutes
    while True:
        _t.sleep(INTERVAL)
        try:
            cfg = _load_config()
            url = _resolve_tracker_url(cfg)
            key = cfg.get('tray_api_key', '')
            n = _flush_spool(url, key)
            if n:
                _toast('Tickets Sent',
                       f"{n} ticket(s) saved while offline have now reached IT.")
        except Exception as e:
            _log(f'flush loop error: {e}')


# ── ticket form ─────────────────────────────────────────────────────────────

def _show_ticket_form(config: dict) -> None:
    """Open a tkinter dialog to submit a support ticket."""
    import tkinter as tk
    from tkinter import ttk, messagebox

    # Re-read config on every open so the tray picks up URL changes written
    # by the agent (e.g. after the user moves between office LAN and remote).
    cfg = _load_config() or config
    tracker_url = _resolve_tracker_url(cfg)
    api_key     = cfg.get('tray_api_key', '')
    asset_id    = cfg.get('asset_id', '')
    hostname    = _get_hostname()
    username    = _get_username()

    root = tk.Tk()
    root.title('Submit IT Ticket')
    root.resizable(False, False)
    root.attributes('-topmost', True)

    # Window icon
    try:
        ico_path = os.path.join(os.path.dirname(sys.argv[0]), '_tray_icon.ico')
        _save_ico(ico_path)
        root.iconbitmap(ico_path)
    except Exception:
        pass

    # ── layout ──────────────────────────────────────────────────────────────
    pad = dict(padx=12, pady=6)

    header = tk.Frame(root, bg='#8B1A2B', height=52)
    header.pack(fill='x')
    tk.Label(header, text='  ◈◈  Submit IT Ticket', bg='#8B1A2B', fg='white',
             font=('Segoe UI', 13, 'bold')).pack(side='left', padx=10, pady=12)

    f = tk.Frame(root, padx=16, pady=12)
    f.pack(fill='both', expand=True)

    # Subject
    tk.Label(f, text='Subject *', font=('Segoe UI', 9, 'bold'), anchor='w').grid(row=0, column=0, sticky='w', **pad)
    subject_var = tk.StringVar()
    subject_entry = ttk.Entry(f, textvariable=subject_var, width=48)
    subject_entry.grid(row=0, column=1, sticky='ew', **pad)

    # Priority
    tk.Label(f, text='Priority', font=('Segoe UI', 9, 'bold'), anchor='w').grid(row=1, column=0, sticky='w', **pad)
    priority_var = tk.StringVar(value='Normal')
    ttk.Combobox(f, textvariable=priority_var, values=['Low', 'Normal', 'High', 'Urgent'],
                 state='readonly', width=12).grid(row=1, column=1, sticky='w', **pad)

    # Description
    tk.Label(f, text='Description *', font=('Segoe UI', 9, 'bold'), anchor='w').grid(row=2, column=0, sticky='nw', **pad)
    desc_text = tk.Text(f, width=46, height=6, font=('Segoe UI', 9), wrap='word')
    desc_text.grid(row=2, column=1, sticky='ew', **pad)

    # Reporter info (pre-filled, editable)
    tk.Label(f, text='Your Name', font=('Segoe UI', 9), anchor='w', fg='#555').grid(row=3, column=0, sticky='w', **pad)
    name_var = tk.StringVar(value=username)
    ttk.Entry(f, textvariable=name_var, width=48).grid(row=3, column=1, sticky='ew', **pad)

    tk.Label(f, text='Email (optional)', font=('Segoe UI', 9), anchor='w', fg='#555').grid(row=4, column=0, sticky='w', **pad)
    email_var = tk.StringVar()
    ttk.Entry(f, textvariable=email_var, width=48).grid(row=4, column=1, sticky='ew', **pad)

    # Status label
    status_var = tk.StringVar()
    tk.Label(f, textvariable=status_var, fg='#555', font=('Segoe UI', 8)).grid(row=5, column=0, columnspan=2, sticky='w', padx=12)

    def _submit():
        subj = subject_var.get().strip()
        desc = desc_text.get('1.0', 'end').strip()
        if not subj:
            messagebox.showwarning('Missing Subject', 'Please enter a subject.', parent=root)
            return
        if not desc:
            messagebox.showwarning('Missing Description', 'Please describe the issue.', parent=root)
            return
        if not tracker_url or not api_key:
            messagebox.showerror('Not Configured', 'Tray config is missing. Contact IT.', parent=root)
            return

        btn_submit.config(state='disabled', text='Submitting…')
        status_var.set('Sending…')
        root.update()

        payload = {
            'subject':      subj,
            'description':  desc,
            'priority':     priority_var.get(),
            'source':       'tray',
            'reporter_name': name_var.get().strip() or None,
            'reporter_email': email_var.get().strip() or None,
            'hostname':     hostname,
            'asset_id':     asset_id or None,
        }
        try:
            ticket_id = _post_ticket(tracker_url, api_key, payload)
            status_var.set(f'✓ Ticket #{ticket_id} submitted!')
            _toast('Ticket Submitted', f"IT received your request (#{ticket_id}). We'll be in touch soon.")
            # Opportunistic: connectivity is clearly up, so drain anything that
            # was queued during an earlier outage.
            _run_in_thread(_flush_spool, tracker_url, api_key)
            root.after(1800, root.destroy)
        except Exception as e:
            # Server unreachable / timeout / non-2xx — never lose the ticket.
            _log(f'submit failed, spooling: {e}')
            _spool_ticket(payload)
            status_var.set("No connection to IT right now — saved, will send when you're back online.")
            _toast('Ticket Saved',
                   "No connection to IT right now — your ticket is saved and "
                   "will send automatically once you're back online.")
            root.after(2600, root.destroy)

    # Buttons
    btn_frame = tk.Frame(f)
    btn_frame.grid(row=6, column=0, columnspan=2, pady=(6, 2))
    btn_submit = tk.Button(btn_frame, text='Submit Ticket', bg='#8B1A2B', fg='white',
                           font=('Segoe UI', 10, 'bold'), padx=16, pady=6,
                           relief='flat', cursor='hand2', command=_submit)
    btn_submit.pack(side='left', padx=6)
    tk.Button(btn_frame, text='Cancel', font=('Segoe UI', 10), padx=12, pady=6,
              relief='flat', command=root.destroy).pack(side='left', padx=6)

    f.columnconfigure(1, weight=1)
    subject_entry.focus_set()
    # Delay Return binding so tray-menu Enter key doesn't trigger submit on open
    root.after(300, lambda: root.bind('<Return>', lambda e: _submit()))
    root.mainloop()


# ── computer info dialog ─────────────────────────────────────────────────────

def _show_info_dialog(config: dict) -> None:
    import tkinter as tk
    from tkinter import ttk

    cfg = _load_config() or config
    hostname  = _get_hostname()
    username  = _get_username()
    asset_tag = cfg.get('asset_tag', 'Unknown')
    it_contact = cfg.get('it_contact', 'IT Support')
    reboot    = _pending_reboot()

    root = tk.Tk()
    root.title('Computer Info')
    root.resizable(False, False)
    root.attributes('-topmost', True)

    try:
        ico_path = os.path.join(os.path.dirname(sys.argv[0]), '_tray_icon.ico')
        _save_ico(ico_path)
        root.iconbitmap(ico_path)
    except Exception:
        pass

    header = tk.Frame(root, bg='#8B1A2B', height=52)
    header.pack(fill='x')
    tk.Label(header, text='  ◈◈  Computer Info', bg='#8B1A2B', fg='white',
             font=('Segoe UI', 13, 'bold')).pack(side='left', padx=10, pady=12)

    f = tk.Frame(root, padx=20, pady=16)
    f.pack(fill='both')

    rows = [
        ('Hostname',   hostname),
        ('Logged in as', username),
        ('Asset Tag',  asset_tag),
        ('IT Contact', it_contact),
        ('Pending Reboot', '⚠ Yes — please reboot when convenient' if reboot else 'No'),
    ]
    for i, (label, value) in enumerate(rows):
        tk.Label(f, text=label + ':', font=('Segoe UI', 9, 'bold'), anchor='w', width=18).grid(row=i, column=0, sticky='w', pady=4)
        color = '#c0392b' if '⚠' in value else '#222'
        tk.Label(f, text=value, font=('Segoe UI', 9), anchor='w', fg=color).grid(row=i, column=1, sticky='w', pady=4, padx=8)

    tk.Button(f, text='Close', font=('Segoe UI', 10), padx=14, pady=5,
              relief='flat', command=root.destroy).grid(row=len(rows), column=0, columnspan=2, pady=(12, 0))

    root.mainloop()


# ── updates dialog ───────────────────────────────────────────────────────────

def _show_updates_dialog() -> None:
    import tkinter as tk
    from tkinter import messagebox

    reboot = _pending_reboot()

    root = tk.Tk()
    root.withdraw()

    if reboot:
        if messagebox.askyesno('Pending Reboot', 'Your computer has a pending reboot.\nWould you like to restart now?', parent=root):
            subprocess.run(['shutdown', '/r', '/t', '60', '/c', 'Restarting for pending updates. You have 60 seconds to save your work.'])
    else:
        messagebox.showinfo('Updates', 'No pending reboot detected.\nWindows Update will install updates automatically.', parent=root)

    root.destroy()


# ── tray setup ───────────────────────────────────────────────────────────────

def _run_in_thread(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def _acquire_single_instance_mutex():
    """Create a named Windows mutex. Returns the handle (keep alive) or exits if already running."""
    _MUTEX_NAME = "Global\\CirqueTrayMutex"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    # ERROR_ALREADY_EXISTS == 183
    if ctypes.windll.kernel32.GetLastError() == 183:
        _log('Mutex already held — another tray instance is running, exiting')
        sys.exit(0)
    return handle  # keep alive for process lifetime


def main():
    _mutex = _acquire_single_instance_mutex()  # exit if another tray is running

    try:
        import pystray
        from PIL import Image
    except ImportError:
        # Try to self-install missing dependencies then retry
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--quiet', 'pystray', 'pillow'],
                timeout=120, check=False,
            )
        except Exception:
            pass
        try:
            import pystray
            from PIL import Image
        except ImportError:
            sys.exit(0)  # still missing, give up silently

    config = _load_config()
    reboot = _pending_reboot()

    # Background retry: flush any tickets that were spooled during an outage.
    # The tray is a persistent process (pystray icon below blocks on icon.run()),
    # so this daemon thread lives for the whole session and retries on a timer.
    threading.Thread(target=_flush_spool_loop, daemon=True, name='ticket-flush').start()

    # Build menu
    reboot_label = '⚠ Pending Reboot!' if reboot else 'Check for Updates'

    menu = pystray.Menu(
        pystray.MenuItem('Submit IT Ticket',
                         lambda icon, item: _run_in_thread(_show_ticket_form, config),
                         default=True),
        pystray.MenuItem('Open IT Portal',
                         lambda icon, item: webbrowser.open(_resolve_tracker_url(_load_config()))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Computer Info',
                         lambda icon, item: _run_in_thread(_show_info_dialog, config)),
        pystray.MenuItem(reboot_label,
                         lambda icon, item: _run_in_thread(_show_updates_dialog)),
    )

    icon_img = _get_icon_image()
    icon = pystray.Icon('CirqueIT', icon_img, 'Cirque IT Support', menu)
    _log(f'Starting tray icon (pystray {getattr(pystray, "__version__", "?")}), pythonw={sys.executable}')
    try:
        icon.run()
    except Exception as _e:
        _log(f'icon.run() failed: {_e}\n{traceback.format_exc()}')
        sys.exit(1)


if __name__ == '__main__':
    _log('tray.py started')
    main()
