"""Director: one voice, limited airtime, so it decides what is said and when.

Speech is slower than football. A commentator speaks about 2.5 words a second;
a single attack can contain ten events in five seconds. The Director keeps one
speaking channel and, every time it is free:

  1. drops queued lines that are now too old to be useful (each has a max delay),
  2. speaks the highest-priority remaining line,
  3. if nothing is queued, describes where the ball is now (only if it changed),
  4. in stoppages and safe spells, lets the Colour agent's context lines in.

Urgent lines (goals, penalties, red cards, big chances) can interrupt whatever
is being said. Everything is decided causally, with no knowledge of what comes next.
"""

from dataclasses import dataclass, field

WORDS_PER_SECOND = 2.6
GAP = 0.35                    # breath between lines
BALL_LINE_MIN_GAP = 2.5       # don't narrate ball position more often than this
COLOUR_MAX_DANGER = 0.25      # colour only when the ball is in a safe area
SOFT_INTERRUPT_PRIORITY = 55   # lines at or above this may cut off ball narration
BALL_LIVE_WINDOW = 4.0        # narrate the ball only within this many seconds of the last event


def duration(text, excite=0.0):
    words = max(1, len(text.split()))
    return words / WORDS_PER_SECOND * (1.08 if excite >= 1.5 else 1.0) + 0.25


@dataclass
class Utterance:
    start: float
    end: float
    text: str
    kind: str
    priority: int
    excite: float
    team: str
    born: float
    interrupt: bool = False
    pan: float = 0.0
    cut_at: float = None
    source: str = "describer"
    meta: dict = field(default_factory=dict)


class Director:
    def __init__(self, state, describer):
        self.s = state
        self.d = describer
        self.queue = []
        self.timeline = []
        self.free_at = -1e9
        self.last_ball_text = None
        self.last_ball_at = -1e9
        self.last_ball_poss = None
        self.dropped = []
        self.interrupted = 0

    def offer(self, lines):
        now = self.s.clock
        for ln in lines:
            if self.free_at > ln.t + 0.1 and self.timeline:
                cur = self.timeline[-1]
                # hard interrupt for urgent lines; soft interrupt of routine ball narration for anything important
                urgent = ln.interrupt and cur.priority < ln.priority
                soft = cur.kind == "ball" and ln.priority >= SOFT_INTERRUPT_PRIORITY
                if (urgent or soft) and cur.start < ln.t:
                    cur.cut_at = ln.t
                    self.free_at = ln.t - GAP
                    self.interrupted += 1
            self.queue.append(ln)
        self._fill(now, allow_ball=False)

    def advance(self, now):
        """Called before the event at time `now` is applied: use the air time up to now."""
        self._fill(now, allow_ball=True)

    def _pan(self):
        return round(max(-1.0, min(1.0, (self.s.ball_x - 52.5) / 52.5)) * 0.7, 2)

    def _fill(self, now, allow_ball):
        while True:
            slot = max(self.free_at + GAP, -1e8)
            # drop stale
            keep = []
            for ln in self.queue:
                start = max(slot, ln.t)
                if start - ln.t > ln.max_delay:
                    self.dropped.append(ln)
                else:
                    keep.append(ln)
            self.queue = keep
            ready = [ln for ln in self.queue if ln.t <= max(slot, now)]
            if ready:
                danger = self.s.danger()
                dead = max(slot, self.s.last_event_clock) - self.s.last_event_clock > 3.0 or self.s.dead_ball
                eligible = [ln for ln in ready if not (ln.kind.startswith("colour") and danger > COLOUR_MAX_DANGER
                                                       and not dead)]
                if eligible:
                    best = max(eligible, key=lambda ln: (ln.priority, -ln.t))
                    start = max(slot, best.t)
                    if start > now + 0.01 and not best.interrupt:
                        return
                    self.queue.remove(best)
                    self._speak(best, start)
                    continue
            if not allow_ball:
                return
            # nothing urgent: narrate the ball if the channel has been free a moment
            # never narrate the ball before the event that put it there
            start = max(slot, self.last_ball_at + BALL_LINE_MIN_GAP, self.s.last_event_clock)
            if start > now:
                return
            # only while play is live: no events for a few seconds means the ball is dead
            if start - self.s.last_event_clock > BALL_LIVE_WINDOW or self.s.dead_ball:
                return
            text = self.d.ball_line(self.last_ball_text, self.last_ball_at, start, self.last_ball_poss)
            if not text:
                return
            self.last_ball_text = text
            self.last_ball_at = start
            self.last_ball_poss = self.s.poss_team
            self._speak_text(text, start, "ball", 20, 0.0, self.s.poss_team)
            return

    def _speak(self, ln, start):
        u = Utterance(start=round(start, 2), end=round(start + duration(ln.text, ln.excite), 2), text=ln.text,
                      kind=ln.kind, priority=ln.priority, excite=ln.excite, team=ln.team, born=round(ln.t, 2),
                      interrupt=ln.interrupt, pan=self._pan(), meta=ln.meta,
                      source="colour" if ln.kind.startswith("colour") else "describer")
        self.timeline.append(u)
        self.free_at = u.end

    def _speak_text(self, text, start, kind, prio, excite, team):
        u = Utterance(start=round(start, 2), end=round(start + duration(text, excite), 2), text=text, kind=kind,
                      priority=prio, excite=excite, team=team, born=round(start, 2), pan=self._pan())
        self.timeline.append(u)
        self.free_at = u.end

    def flush(self, until):
        self._fill(until, allow_ball=False)
