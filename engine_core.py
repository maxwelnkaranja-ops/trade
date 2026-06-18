"""
engine_core.py — Benz Club Trading Engine Core
Faithful Python port of the JS analysis + strategy logic from index.html.
All math is 1-to-1 with the browser version.
"""

from __future__ import annotations
import math
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_even(d: int) -> bool:
    return d % 2 == 0

def is_odd(d: int) -> bool:
    return d % 2 != 0


# ---------------------------------------------------------------------------
# analyzeDigits — EvenOdd flavour
# ---------------------------------------------------------------------------

def analyze_digits_evenodd(history: list[int], tick_period: int = 50) -> dict:
    data = history[-tick_period:]
    if len(data) < 5:
        return _empty_analysis_evenodd()

    counts = [0] * 10
    for d in data:
        counts[d] += 1
    percentages = [c / len(data) * 100 for c in counts]

    hot_digits  = [i for i, p in enumerate(percentages) if p > 15]
    cold_digits = [i for i, p in enumerate(percentages) if p < 8]

    recent_weight = 3
    weighted_odd = 0.0
    total_weight = 0.0
    half = len(data) / 2
    for idx, digit in enumerate(data):
        w = 1 if idx < half else recent_weight
        total_weight += w
        if is_odd(digit):
            weighted_odd += w
    trend_score = (weighted_odd / total_weight) * 100

    recent_slice   = data[-10:]
    previous_slice = data[-20:-10]
    recent_odd_rate   = sum(1 for d in recent_slice   if is_odd(d)) / max(len(recent_slice), 1)
    previous_odd_rate = sum(1 for d in previous_slice if is_odd(d)) / max(len(previous_slice), 1) if previous_slice else 0.5
    momentum_score = ((recent_odd_rate - previous_odd_rate) + 1) * 50

    volatility       = _calc_volatility(data)
    volatility_index = max(0.0, 100 - volatility * 10)
    odd_digit_weight = sum(percentages[i] for i in range(10) if is_odd(i))

    rare_digits   = len(cold_digits) >= 4
    dominant_odd  = odd_digit_weight > 55
    stable_pattern = len(data) >= 25
    momentum_up   = momentum_score > 55
    low_volatility = volatility_index > 50

    probability = 0.0
    probability += min(30.0, odd_digit_weight / 2)
    if trend_score > 55:   probability += 20
    elif trend_score > 50: probability += 10
    if momentum_up:    probability += 15
    if stable_pattern: probability += 10
    if low_volatility: probability += 10
    if rare_digits:    probability += 15

    recommendation = 'HOLD'
    if probability >= 85: recommendation = 'STRONG_BUY'
    elif probability >= 70: recommendation = 'BUY'
    elif probability < 50:  recommendation = 'AVOID'

    return {
        'probability': min(100, round(probability)),
        'digitCounts': counts,
        'digitPercentages': percentages,
        'hotDigits': hot_digits,
        'coldDigits': cold_digits,
        'trendScore': round(trend_score),
        'momentumScore': round(momentum_score),
        'volatilityIndex': round(volatility_index),
        'oddDigitWeight': round(odd_digit_weight),
        'conditions': {
            'rareDigits': rare_digits,
            'dominantOdd': dominant_odd,
            'stablePattern': stable_pattern,
            'momentumUp': momentum_up,
            'lowVolatility': low_volatility,
        },
        'recommendation': recommendation,
        'bestBarrier': 0,
    }


def _empty_analysis_evenodd() -> dict:
    return {
        'probability': 0,
        'digitCounts': [0]*10,
        'digitPercentages': [0.0]*10,
        'hotDigits': [], 'coldDigits': [],
        'trendScore': 50, 'momentumScore': 50,
        'volatilityIndex': 50, 'oddDigitWeight': 50,
        'conditions': {
            'rareDigits': False, 'dominantOdd': False,
            'stablePattern': False, 'momentumUp': False, 'lowVolatility': False,
        },
        'recommendation': 'HOLD', 'bestBarrier': 0,
    }


# ---------------------------------------------------------------------------
# analyzeDigits — Over/Under flavour  (NexusTrading)
# ---------------------------------------------------------------------------

def analyze_digits_over(history: list[int], tick_period: int = 50) -> dict:
    data = history[-tick_period:]
    if len(data) < 5:
        return _empty_analysis_over()

    counts = [0] * 10
    for d in data:
        counts[d] += 1
    percentages = [c / len(data) * 100 for c in counts]

    hot_digits  = [i for i, p in enumerate(percentages) if p > 15]
    cold_digits = [i for i, p in enumerate(percentages) if p < 8]

    recent_weight = 3
    weighted_over = 0.0
    total_weight  = 0.0
    half = len(data) / 2
    for idx, digit in enumerate(data):
        w = 1 if idx < half else recent_weight
        total_weight += w
        if digit > 3:
            weighted_over += w
    trend_score = (weighted_over / total_weight) * 100

    recent_slice   = data[-10:]
    previous_slice = data[-20:-10]
    recent_over_rate   = sum(1 for d in recent_slice   if d > 3) / max(len(recent_slice), 1)
    previous_over_rate = sum(1 for d in previous_slice if d > 3) / max(len(previous_slice), 1) if previous_slice else 0.5
    momentum_score = ((recent_over_rate - previous_over_rate) + 1) * 50

    volatility       = _calc_volatility(data)
    volatility_index = max(0.0, 100 - volatility * 10)
    over_digit_weight = sum(percentages[i] for i in range(4, 10))

    rare_digits    = len(cold_digits) >= 4
    dominant_over  = over_digit_weight > 60
    stable_pattern = len(data) >= 25
    momentum_up    = momentum_score > 60
    low_volatility = volatility_index > 50

    probability = 0.0
    probability += min(30.0, over_digit_weight / 2)
    if trend_score > 55:   probability += 20
    elif trend_score > 50: probability += 10
    if momentum_up:    probability += 15
    if stable_pattern: probability += 10
    if low_volatility: probability += 10
    if rare_digits:    probability += 15

    best_barrier = 3
    if probability >= 85 and trend_score > 60: best_barrier = 4
    elif probability < 60: best_barrier = 2

    recommendation = 'HOLD'
    if probability >= 85: recommendation = 'STRONG_BUY'
    elif probability >= 70: recommendation = 'BUY'
    elif probability < 50:  recommendation = 'AVOID'

    return {
        'probability': min(100, round(probability)),
        'digitCounts': counts,
        'digitPercentages': percentages,
        'hotDigits': hot_digits,
        'coldDigits': cold_digits,
        'trendScore': round(trend_score),
        'momentumScore': round(momentum_score),
        'volatilityIndex': round(volatility_index),
        'overDigitWeight': round(over_digit_weight),
        'conditions': {
            'rareDigits': rare_digits,
            'dominantOver': dominant_over,
            'stablePattern': stable_pattern,
            'momentumUp': momentum_up,
            'lowVolatility': low_volatility,
        },
        'recommendation': recommendation,
        'bestBarrier': best_barrier,
    }


def _empty_analysis_over() -> dict:
    return {
        'probability': 0,
        'digitCounts': [0]*10,
        'digitPercentages': [0.0]*10,
        'hotDigits': [], 'coldDigits': [],
        'trendScore': 50, 'momentumScore': 50,
        'volatilityIndex': 50, 'overDigitWeight': 50,
        'conditions': {
            'rareDigits': False, 'dominantOver': False,
            'stablePattern': False, 'momentumUp': False, 'lowVolatility': False,
        },
        'recommendation': 'HOLD', 'bestBarrier': 3,
    }


def _calc_volatility(data: list[int]) -> float:
    if len(data) < 2:
        return 0.0
    return sum(abs(data[i] - data[i-1]) for i in range(1, len(data))) / (len(data) - 1)


# ---------------------------------------------------------------------------
# Multi-timeframe
# ---------------------------------------------------------------------------

def analyze_multi_timeframe(history: list[int], mode: str = 'over') -> dict:
    fn = analyze_digits_evenodd if mode == 'evenodd' else analyze_digits_over
    short_a = fn(history, 25)
    long_a  = fn(history, 50)

    short_ok = short_a['probability'] >= 50 and short_a['recommendation'] != 'AVOID'
    long_ok  = long_a['probability']  >= 50 and long_a['recommendation']  != 'AVOID'

    aligned = (
        (short_ok and long_ok) or
        (short_a['recommendation'] == 'STRONG_BUY' and long_a['recommendation'] != 'AVOID') or
        (long_a['recommendation']  == 'STRONG_BUY' and short_a['recommendation'] != 'AVOID')
    )
    confidence = round(short_a['probability'] * 0.6 + long_a['probability'] * 0.4)
    return {'short': short_a, 'long': long_a, 'aligned': aligned, 'confidence': confidence}


# ---------------------------------------------------------------------------
# Entry condition checkers
# ---------------------------------------------------------------------------

def check_entry_conditions_evenodd(history: list[int], analysis: dict,
                                   min_streak: int = 2, min_probability: int = 80) -> dict:
    streak = 0
    for d in reversed(history):
        if is_even(d): streak += 1
        else: break

    if streak < min_streak:
        return {'canEnter': False, 'streak': streak, 'reason': f'Even Streak {streak}/{min_streak}'}
    if analysis['probability'] < min_probability:
        return {'canEnter': False, 'streak': streak, 'reason': f"Prob {analysis['probability']}%/{min_probability}%"}
    if analysis['recommendation'] == 'AVOID':
        return {'canEnter': False, 'streak': streak, 'reason': 'Analysis: AVOID'}

    if len(history) >= 50:
        mtf = analyze_multi_timeframe(history, 'evenodd')
        if not mtf['aligned']:
            return {'canEnter': False, 'streak': streak,
                    'reason': f"MTF Divergence (S:{mtf['short']['probability']}% L:{mtf['long']['probability']}%)",
                    'multiTimeframe': mtf}
        return {'canEnter': True, 'streak': streak, 'reason': 'MTF ALIGNED', 'multiTimeframe': mtf}

    return {'canEnter': True, 'streak': streak, 'reason': 'CONDITIONS MET'}


def check_entry_conditions_over(history: list[int], analysis: dict,
                                min_streak: int = 2, min_probability: int = 80,
                                barrier: int = 3) -> dict:
    streak = 0
    for d in reversed(history):
        if d <= barrier: streak += 1
        else: break

    if streak < min_streak:
        return {'canEnter': False, 'streak': streak, 'reason': f'Streak {streak}/{min_streak}'}
    if analysis['probability'] < min_probability:
        return {'canEnter': False, 'streak': streak, 'reason': f"Prob {analysis['probability']}%/{min_probability}%"}
    if analysis['recommendation'] == 'AVOID':
        return {'canEnter': False, 'streak': streak, 'reason': 'Analysis: AVOID'}

    if len(history) >= 50:
        mtf = analyze_multi_timeframe(history, 'over')
        if not mtf['aligned']:
            return {'canEnter': False, 'streak': streak,
                    'reason': f"MTF Divergence (S:{mtf['short']['probability']}% L:{mtf['long']['probability']}%)",
                    'multiTimeframe': mtf}
        return {'canEnter': True, 'streak': streak, 'reason': 'MTF ALIGNED', 'multiTimeframe': mtf}

    return {'canEnter': True, 'streak': streak, 'reason': 'CONDITIONS MET'}


# ---------------------------------------------------------------------------
# Martingale calculator
# ---------------------------------------------------------------------------

def calculate_next_stake(current_stake: float, multiplier: float,
                         step: int, max_step: int) -> dict:
    if step >= max_step and max_step != 999:
        return {'stake': current_stake, 'shouldStop': True}
    return {'stake': round(current_stake * multiplier, 2), 'shouldStop': False}


# ---------------------------------------------------------------------------
# Market health  (used by ticker, same logic as JS GlobalSocketManager)
# ---------------------------------------------------------------------------

def calculate_health_evenodd(short_history: list[int], long_history: list[int]) -> dict | None:
    if len(short_history) < 10 or len(long_history) < 20:
        return None
    recent_ticks = short_history[-50:]
    short_term_odd_rate = sum(1 for d in recent_ticks if is_odd(d)) / len(recent_ticks) * 100
    long_term_odd_rate  = sum(1 for d in long_history if is_odd(d)) / len(long_history) * 100

    recent20  = long_history[-20:]
    previous40 = long_history[-60:-20]
    recent_odd    = sum(1 for d in recent20    if is_odd(d)) / len(recent20)
    previous_odd  = (sum(1 for d in previous40 if is_odd(d)) / len(previous40)) if previous40 else 0.5
    trend = 'STABLE'
    if recent_odd - previous_odd > 0.1:  trend = 'UP'
    elif recent_odd - previous_odd < -0.1: trend = 'DOWN'

    changes  = sum(abs(recent_ticks[i] - recent_ticks[i-1]) for i in range(1, len(recent_ticks)))
    avg_change = changes / max(len(recent_ticks) - 1, 1)
    volatility = 'HIGH' if avg_change > 4 else ('MEDIUM' if avg_change > 2.5 else 'LOW')

    score = 0
    if 45 <= long_term_odd_rate <= 65: score += 40
    elif long_term_odd_rate >= 40:     score += 25
    else:                              score += 10
    if trend == 'UP':     score += 30
    elif trend == 'STABLE': score += 15
    if volatility == 'LOW':    score += 30
    elif volatility == 'MEDIUM': score += 15

    long_term = 'POOR'
    if score >= 70:   long_term = 'HEALTHY'
    elif score >= 45: long_term = 'MODERATE'

    return {
        'shortTerm': round(short_term_odd_rate),
        'longTerm': long_term,
        'longTermScore': round(score),
        'oddRate': round(long_term_odd_rate),
        'trend': trend,
        'volatility': volatility,
    }


def calculate_health_over(short_history: list[int], long_history: list[int]) -> dict | None:
    if len(short_history) < 10 or len(long_history) < 20:
        return None
    recent_ticks = short_history[-50:]
    short_term_over_rate = sum(1 for d in recent_ticks if d > 3) / len(recent_ticks) * 100
    long_term_over_rate  = sum(1 for d in long_history if d > 3) / len(long_history) * 100

    recent20   = long_history[-20:]
    previous40 = long_history[-60:-20]
    recent_over   = sum(1 for d in recent20   if d > 3) / len(recent20)
    previous_over = (sum(1 for d in previous40 if d > 3) / len(previous40)) if previous40 else 0.5
    trend = 'STABLE'
    if recent_over - previous_over > 0.1:  trend = 'UP'
    elif recent_over - previous_over < -0.1: trend = 'DOWN'

    changes    = sum(abs(recent_ticks[i] - recent_ticks[i-1]) for i in range(1, len(recent_ticks)))
    avg_change = changes / max(len(recent_ticks) - 1, 1)
    volatility = 'HIGH' if avg_change > 4 else ('MEDIUM' if avg_change > 2.5 else 'LOW')

    score = 0
    if 55 <= long_term_over_rate <= 75: score += 40
    elif long_term_over_rate >= 50:     score += 25
    else:                               score += 10
    if trend == 'UP':     score += 30
    elif trend == 'STABLE': score += 15
    if volatility == 'LOW':    score += 30
    elif volatility == 'MEDIUM': score += 15

    long_term = 'POOR'
    if score >= 70:   long_term = 'HEALTHY'
    elif score >= 45: long_term = 'MODERATE'

    return {
        'shortTerm': round(short_term_over_rate),
        'longTerm': long_term,
        'longTermScore': round(score),
        'overRate': round(long_term_over_rate),
        'trend': trend,
        'volatility': volatility,
    }


# ---------------------------------------------------------------------------
# EVEN/ODD STRATEGIES  (EvenStrategies / mirror for Odd engine)
# ---------------------------------------------------------------------------

def _check_entry_even_flood(history: list[int]) -> dict:
    if len(history) < 30:
        return {'canEnter': False, 'reason': 'Flood: Need 30+ ticks'}
    last30 = history[-30:]
    even_pct = round(sum(1 for d in last30 if is_even(d)) / len(last30) * 100)
    if even_pct < 60:
        return {'canEnter': False, 'reason': f'Flood: Even at {even_pct}% (need 60%+)'}
    last5 = history[-5:]
    if not all(is_even(d) for d in last5):
        n = sum(1 for d in last5 if is_even(d))
        return {'canEnter': False, 'reason': f'Flood: {n}/5 even in last 5 (need 5/5)'}
    return {'canEnter': True, 'reason': f'FLOOD: Even {even_pct}% in 30T | Last 5 ALL EVEN → ODD correction due'}


def _check_entry_odd_bounce(history: list[int]) -> dict:
    if len(history) < 15:
        return {'canEnter': False, 'reason': 'Bounce: Need 15+ ticks'}
    tl, sl, l = history[-3], history[-2], history[-1]
    if is_even(tl) and is_odd(sl) and is_even(l):
        v_count = v_success = 0
        for i in range(2, len(history) - 1):
            if is_even(history[i-2]) and is_odd(history[i-1]) and is_even(history[i]):
                v_count += 1
                if is_odd(history[i+1]): v_success += 1
        if v_count < 3:
            return {'canEnter': False, 'reason': f'Bounce: Pattern found but only {v_count}/3 past occurrences'}
        sr = round(v_success / v_count * 100)
        if sr < 40:
            return {'canEnter': False, 'reason': f'Bounce: Success rate {sr}% (need 40%+)'}
        return {'canEnter': True, 'reason': f'BOUNCE: E→O→E pattern! {v_success}/{v_count} followed by ODD ({sr}%)'}
    p = ('E' if is_even(tl) else 'O') + '→' + ('O' if is_odd(sl) else 'E') + '→' + ('E' if is_even(l) else 'O')
    return {'canEnter': False, 'reason': f'Bounce: Waiting for E→O→E (got {p})'}


def _check_entry_parity_switch(history: list[int]) -> dict:
    if len(history) < 10:
        return {'canEnter': False, 'reason': 'Switch: Need 10+ ticks'}
    last5 = history[-5:]
    if not all(is_even(d) for d in last5):
        n = sum(1 for d in last5 if is_even(d))
        return {'canEnter': False, 'reason': f'Switch: {n}/5 even in last 5 (need 5/5)'}
    last20 = history[-20:]
    odd_pct = round(sum(1 for d in last20 if is_odd(d)) / len(last20) * 100)
    if odd_pct > 30:
        return {'canEnter': False, 'reason': f'Switch: Odd digits {odd_pct}% (need <30% starved)'}
    return {'canEnter': True, 'reason': f'SWITCH: 5 consecutive EVEN | Odds starved {odd_pct}% → SWITCH DUE'}


def _check_entry_ghost_digit(history: list[int]) -> dict:
    if len(history) < 15:
        return {'canEnter': False, 'reason': 'Ghost: Need 15+ ticks'}
    n = len(history)
    if n >= 4:
        ghost = history[n-4]
        if is_odd(ghost):
            gap = history[n-3:]
            if all(is_even(d) for d in gap):
                gc = 0
                for i in range(4, len(history)):
                    if (is_odd(history[i-4]) and is_even(history[i-3]) and
                            is_even(history[i-2]) and is_even(history[i-1]) and is_odd(history[i])):
                        gc += 1
                if gc < 2:
                    return {'canEnter': False, 'reason': f'Ghost: Odd {ghost} ghosted but only {gc}/2 past confirmations'}
                return {'canEnter': True, 'reason': f'GHOST: Odd {ghost} → 3 EVEN gap ({",".join(str(d) for d in gap)}) | {gc} past returns'}
    last3 = history[-3:]
    idx = next((i for i, d in enumerate(last3) if is_odd(d)), -1)
    if idx != -1:
        return {'canEnter': False, 'reason': f'Ghost: Odd appeared {3 - idx} tick(s) ago — need 3+ even gap'}
    return {'canEnter': False, 'reason': 'Ghost: No odd→even suppression detected'}


def _check_entry_double_tap_even(history: list[int]) -> dict:
    if len(history) < 20:
        return {'canEnter': False, 'reason': 'D-Tap: Need 20+ ticks'}
    last6 = history[-6:]
    if is_odd(last6[-1]):
        return {'canEnter': False, 'reason': f'D-Tap: Last digit {last6[-1]} is odd (need even for dip)'}
    last5 = last6[:5]
    for target in range(9, 0, -2):  # odd: 9,7,5,3,1
        positions = [i for i, d in enumerate(last5) if d == target]
        if len(positions) == 2 and positions[1] - positions[0] >= 2:
            last20 = history[-20:]
            total = last20.count(target)
            if total < 3:
                return {'canEnter': False, 'reason': f'D-Tap: {target} appeared {total}/3 in 20T (weak pull)'}
            return {'canEnter': True, 'reason': f'D-TAP: {target} 2x in 5T (gap {positions[1]-positions[0]}) | {total}x in 20T → 3rd ODD tap'}
    return {'canEnter': False, 'reason': 'D-Tap: No double-tap pattern found'}


def _check_entry_rapid_scalp_even(history: list[int]) -> dict:
    if len(history) < 5:
        return {'canEnter': False, 'reason': 'Scalp: Need 5+ ticks'}
    last5  = history[-5:]
    last15 = history[-15:]
    last20 = history[-20:]
    all_even  = all(is_even(d) for d in last5)
    odd_count = sum(1 for d in last15 if is_odd(d))
    consec_odd = any(is_odd(last20[i]) and is_odd(last20[i-1]) for i in range(1, len(last20)))
    if all_even and odd_count <= 2 and not consec_odd:
        return {'canEnter': True, 'reason': f'SCALP: Last 5 ALL EVEN | Odds: {odd_count}/15 | No consecutive odds'}
    if not all_even:    msg = 'Odd in last 5'
    elif odd_count > 2: msg = f'{odd_count} odds in 15T'
    else:               msg = 'Consecutive odds detected'
    return {'canEnter': False, 'reason': f'Scalp: {msg}'}


def _check_entry_odd_starvation(history: list[int]) -> dict:
    if len(history) < 50:
        return {'canEnter': False, 'reason': 'Starve: Need 50+ ticks'}
    last100 = history[-100:]
    odd_pct = round(sum(1 for d in last100 if is_odd(d)) / len(last100) * 100)
    if odd_pct >= 35:
        return {'canEnter': False, 'reason': f'Starve: Odd at {odd_pct}% (need <35% starvation)'}
    last8 = history[-8:]
    if any(is_odd(d) for d in last8):
        return {'canEnter': False, 'reason': 'Starve: Odd seen recently — waiting for 8+ even gap'}
    if sum(1 for d in last100 if is_even(d)) < 50:
        return {'canEnter': False, 'reason': 'Starve: Market too flat'}
    return {'canEnter': True, 'reason': f'STARVE: Odd at {odd_pct}% in 100T | Absent 8+ ticks | ODD CORRECTION DUE'}


def _check_entry_even_avalanche(history: list[int]) -> dict:
    if len(history) < 50:
        return {'canEnter': False, 'reason': 'Avalanche: Need 50+ ticks'}
    last100 = history[-100:]
    even_pct = round(sum(1 for d in last100 if is_even(d)) / len(last100) * 100)
    if even_pct < 65:
        return {'canEnter': False, 'reason': f'Avalanche: Even at {even_pct}% (need >65% dominance)'}
    last20  = history[-20:]
    odd_pct = round(sum(1 for d in last20 if is_odd(d)) / len(last20) * 100)
    if odd_pct > 30:
        return {'canEnter': False, 'reason': f'Avalanche: Odd still {odd_pct}% in last 20 (need <30%)'}
    return {'canEnter': True, 'reason': f'AVALANCHE: Even at {even_pct}% in 100T | Odd starved {odd_pct}% → EVEN RIDE'}


EVEN_STRATEGIES = {
    1: {'name': 'NORMAL MODE',     'shortName': 'NORMAL',    'check': None,
        'defaults': {'stake':0.35,'targetRuns':4,'minSignal':80,'minStreak':3,'martingaleEnabled':False,'martingaleMultiplier':2.1,'martingaleStopStep':3}},
    2: {'name': 'EVEN FLOOD',      'shortName': 'FLOOD',     'check': _check_entry_even_flood,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    3: {'name': 'ODD BOUNCE',      'shortName': 'BOUNCE',    'check': _check_entry_odd_bounce,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    4: {'name': 'PARITY SWITCH',   'shortName': 'SWITCH',    'check': _check_entry_parity_switch,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    5: {'name': 'GHOST DIGIT',     'shortName': 'GHOST',     'check': _check_entry_ghost_digit,
        'defaults': {'stake':0.50,'targetRuns':2,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    6: {'name': 'DOUBLE TAP',      'shortName': 'D-TAP',     'check': _check_entry_double_tap_even,
        'defaults': {'stake':0.45,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.3,'martingaleStopStep':3}},
    7: {'name': 'RAPID SCALP',     'shortName': 'SCALP',     'check': _check_entry_rapid_scalp_even,
        'defaults': {'stake':0.35,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.0,'martingaleStopStep':5}},
    8: {'name': 'ODD STARVATION',  'shortName': 'STARVE',    'check': _check_entry_odd_starvation,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    9: {'name': 'EVEN AVALANCHE',  'shortName': 'AVALANCHE', 'check': _check_entry_even_avalanche,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
}


# ---------------------------------------------------------------------------
# OVER/UNDER STRATEGIES  (NexusStrategies)
# ---------------------------------------------------------------------------

def _check_entry_jump_pattern(history: list[int]) -> dict:
    if len(history) < 30:
        return {'canEnter': False, 'reason': 'Jump: Need 30+ ticks'}
    recent = history[-60:]
    last_digit = recent[-1]
    if last_digit > 4:
        return {'canEnter': False, 'reason': f'Jump: Waiting for bridge digit (got {last_digit})'}
    digit_counts: dict[int, int] = {}
    for d in recent:
        digit_counts[d] = digit_counts.get(d, 0) + 1
    sorted_digits = sorted(digit_counts.items(), key=lambda x: -x[1])
    most_digit = sorted_digits[0][0]
    if most_digit <= 5:
        return {'canEnter': False, 'reason': f'Jump: Top digit {most_digit} (need >5)'}
    last10 = history[-10:]
    high_in_recent = sum(1 for d in last10 if d > 5)
    if high_in_recent < 6:
        return {'canEnter': False, 'reason': f'Jump: High dominance {high_in_recent}/10 (need 6+)'}
    jump_count = sum(1 for i in range(len(recent)-1) if recent[i] == last_digit and recent[i+1] >= 8)
    if jump_count >= 4:
        return {'canEnter': True, 'reason': f'JUMP: {last_digit}→8/9 seen {jump_count}x | Top: {most_digit} | High: {high_in_recent}/10'}
    return {'canEnter': False, 'reason': f'Jump: {last_digit}→8/9 = {jump_count}/4 needed'}


def _check_entry_vshape(history: list[int]) -> dict:
    if len(history) < 15:
        return {'canEnter': False, 'reason': 'V-Shape: Need 15+ ticks'}
    sl, l = history[-2], history[-1]
    if sl == 0 and l == 1:
        v_count = v_success = 0
        for i in range(1, len(history) - 2):
            if history[i-1] == 0 and history[i] == 1:
                v_count += 1
                if history[i+1] >= 7: v_success += 1
        if v_count < 3:
            return {'canEnter': False, 'reason': f'V-Shape: 0→1 detected but only {v_count}/3 past patterns'}
        sr = round(v_success / v_count * 100)
        if sr < 40:
            return {'canEnter': False, 'reason': f'V-Shape: Success rate {sr}% (need 40%+)'}
        return {'canEnter': True, 'reason': f'V-SHAPE: 0→1 FIRED! | {v_success}/{v_count} hit 7+ ({sr}%)'}
    if l == 0:
        return {'canEnter': False, 'reason': 'V-Shape: 0 detected, waiting for 1...'}
    return {'canEnter': False, 'reason': f'V-Shape: Waiting for 0→1 (got {sl}→{l})'}


def _check_entry_binary_switch(history: list[int]) -> dict:
    if len(history) < 10:
        return {'canEnter': False, 'reason': 'Switch: Need 10+ ticks'}
    last6  = history[-6:]
    first5 = last6[:5]
    if not all(4 <= d <= 6 for d in first5):
        n = sum(1 for d in first5 if 4 <= d <= 6)
        return {'canEnter': False, 'reason': f'Switch: {n}/5 in middle zone (need 5/5)'}
    last20   = history[-20:]
    high_pct = round(sum(1 for d in last20 if d >= 7) / len(last20) * 100)
    if high_pct > 30:
        return {'canEnter': False, 'reason': f'Switch: High digits {high_pct}% (need <30% starved)'}
    breakout = last6[5]
    prev     = last6[4]
    if breakout > prev and breakout >= 7:
        return {'canEnter': True, 'reason': f'SWITCH: {",".join(str(d) for d in first5)} → BREAKOUT {breakout}! Highs starved {high_pct}%'}
    if 4 <= breakout <= 6:
        return {'canEnter': False, 'reason': f'Switch: Middle holding {",".join(str(d) for d in last6)} — awaiting breakout'}
    return {'canEnter': False, 'reason': f'Switch: Breakout {breakout} not qualifying'}


def _check_entry_shadow_digit(history: list[int]) -> dict:
    if len(history) < 15:
        return {'canEnter': False, 'reason': 'Shadow: Need 15+ ticks'}
    n = len(history)
    for target in (9, 8):
        if n >= 5:
            ghost_pos = history[n-4]
            if ghost_pos == target:
                gap = history[n-3:]
                all_different  = all(d != target for d in gap)
                all_suppressed = all(d < 6 for d in gap)
                if all_different and all_suppressed:
                    sc = sum(
                        1 for i in range(4, len(history))
                        if history[i-4] == target and
                           all(history[i-k] != target for k in (1,2,3)) and
                           history[i] >= 8
                    )
                    if sc < 2:
                        return {'canEnter': False, 'reason': f'Shadow: {target} ghosted but only {sc}/2 past confirmations'}
                    return {'canEnter': True, 'reason': f'SHADOW: {target} ghosted, gap {",".join(str(d) for d in gap)} (suppressed) | {sc} past shadows'}
    return {'canEnter': False, 'reason': 'Shadow: No ghost pattern (need 8/9→3 suppressed gap)'}


def _check_entry_double_tap_over(history: list[int]) -> dict:
    if len(history) < 20:
        return {'canEnter': False, 'reason': 'D-Tap: Need 20+ ticks'}
    last6 = history[-6:]
    if last6[-1] >= 7:
        return {'canEnter': False, 'reason': f'D-Tap: Last digit {last6[-1]} is high (need low for dip)'}
    last5 = last6[:5]
    for target in range(9, 6, -1):  # 9,8,7
        positions = [i for i, d in enumerate(last5) if d == target]
        if len(positions) == 2 and positions[1] - positions[0] >= 2:
            gap = positions[1] - positions[0]
            last20 = history[-20:]
            total  = last20.count(target)
            if total < 3:
                return {'canEnter': False, 'reason': f'D-Tap: {target} appeared {total}/3 in 20T (weak pull)'}
            return {'canEnter': True, 'reason': f'D-TAP: {target} 2x in 5T (gap {gap}) | {total}x in 20T → 3rd tap'}
    return {'canEnter': False, 'reason': 'D-Tap: No double-tap pattern found'}


def _check_entry_rapid_scalp_over(history: list[int]) -> dict:
    if len(history) < 5:
        return {'canEnter': False, 'reason': 'Scalp: Need 5+ ticks'}
    last5  = history[-5:]
    last15 = history[-15:]
    last20 = history[-20:]
    all_above_zero = all(d > 0 for d in last5)
    zero_count     = sum(1 for d in last15 if d == 0)
    consec_zero    = any(last20[i] == 0 and last20[i-1] == 0 for i in range(1, len(last20)))
    if all_above_zero and zero_count <= 1 and not consec_zero:
        return {'canEnter': True, 'reason': f'SCALP: Last 5 clean | Zeros: {zero_count}/15 | No consecutive 0s'}
    if not all_above_zero: msg = 'Zero in last 5'
    elif zero_count > 1:   msg = f'{zero_count} zeros'
    else:                  msg = 'Consecutive 0s'
    return {'canEnter': False, 'reason': f'Scalp: {msg} detected'}


def _check_entry_digit_starvation(history: list[int]) -> dict:
    if len(history) < 50:
        return {'canEnter': False, 'reason': 'Starve: Need 50+ ticks'}
    last100  = history[-100:]
    high_pct = round(sum(1 for d in last100 if d >= 8) / len(last100) * 100)
    if high_pct >= 10:
        return {'canEnter': False, 'reason': f'Starve: 8+9 at {high_pct}% (need <10% starvation)'}
    last12 = history[-12:]
    if any(d >= 8 for d in last12):
        idx = next(i for i in range(len(last12)-1, -1, -1) if last12[i] >= 8)
        return {'canEnter': False, 'reason': f'Starve: 8/9 seen {len(last12)-1-idx+1} ticks ago (need 12+ gap)'}
    low_pct = round(sum(1 for d in last100 if d <= 2) / len(last100) * 100)
    if low_pct < 15:
        return {'canEnter': False, 'reason': f'Starve: Low digits also scarce ({low_pct}%) — market flat'}
    return {'canEnter': True, 'reason': f'STARVE: 8+9 at {high_pct}% in 100T | Absent 12+ ticks | CORRECTION DUE'}


def _check_entry_avalanche_drop(history: list[int]) -> dict:
    if len(history) < 50:
        return {'canEnter': False, 'reason': 'Avalanche: Need 50+ ticks'}
    last100 = history[-100:]
    low_pct = round(sum(1 for d in last100 if d <= 1) / len(last100) * 100)
    if low_pct >= 8:
        return {'canEnter': False, 'reason': f'Avalanche: 0+1 at {low_pct}% (need <8% starvation)'}
    last10 = history[-10:]
    if any(d <= 1 for d in last10):
        return {'canEnter': False, 'reason': 'Avalanche: 0/1 seen recently — waiting for full absence'}
    if sum(1 for d in last100 if d >= 7) < 15:
        return {'canEnter': False, 'reason': 'Avalanche: Market too flat — highs also low'}
    return {'canEnter': True, 'reason': f'AVALANCHE: 0+1 at {low_pct}% in 100T | Absent 10+ ticks | CRASH DUE'}


OVER_STRATEGIES = {
    1: {'name': 'NORMAL MODE',       'shortName': 'NORMAL',    'check': None,
        'defaults': {'stake':0.35,'targetRuns':4,'minSignal':80,'minStreak':2,'overBarrier':3,'recoveryBarrier':5,'martingaleEnabled':False,'martingaleMultiplier':2.1,'martingaleStopStep':3}},
    2: {'name': 'JUMP PATTERN',      'shortName': 'JUMP',      'check': _check_entry_jump_pattern,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':7,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    3: {'name': 'V-SHAPE RECOVERY',  'shortName': 'V-SHAPE',   'check': _check_entry_vshape,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':7,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    4: {'name': 'BINARY SWITCH',     'shortName': 'SWITCH',    'check': _check_entry_binary_switch,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':7,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    5: {'name': 'SHADOW DIGIT',      'shortName': 'SHADOW',    'check': _check_entry_shadow_digit,
        'defaults': {'stake':0.50,'targetRuns':2,'minSignal':50,'minStreak':0,'overBarrier':8,'recoveryBarrier':6,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    6: {'name': 'DOUBLE TAP',        'shortName': 'D-TAP',     'check': _check_entry_double_tap_over,
        'defaults': {'stake':0.45,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':7,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.3,'martingaleStopStep':3}},
    7: {'name': 'RAPID SCALP',       'shortName': 'SCALP',     'check': _check_entry_rapid_scalp_over,
        'defaults': {'stake':0.35,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':0,'recoveryBarrier':0,'martingaleEnabled':True,'martingaleMultiplier':2.0,'martingaleStopStep':5}},
    8: {'name': 'DIGIT STARVATION',  'shortName': 'STARVE',    'check': _check_entry_digit_starvation,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':7,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
    9: {'name': 'AVALANCHE DROP',    'shortName': 'AVALANCHE', 'check': _check_entry_avalanche_drop,
        'defaults': {'stake':0.50,'targetRuns':1,'minSignal':50,'minStreak':0,'overBarrier':2,'recoveryBarrier':5,'martingaleEnabled':True,'martingaleMultiplier':2.5,'martingaleStopStep':3}},
}
