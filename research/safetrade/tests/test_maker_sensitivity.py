"""Детерминированные тесты sensitivity (шаг 3). Синтетика, без сети.

Покрытие по ТЗ: инверсия стороны (H1/H2), FIFO-очередь, лимит капитала,
частичные исполнения, комиссия, принудительный выход (hold_timeout/window_end),
запрет look-ahead (стакан старше STALE_S не заменяется будущим), задержка
активации.
"""
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maker.sensitivity import (Sensitivity, parse_book, _d, STALE_S, FEE_RATE)

BID = [['1.05', '100']]
ASK = [['1.06', '150']]


def book(ts, bids=None, asks=None):
    return parse_book({'t': int(ts * 1e9), 'bids': bids or BID, 'asks': asks or ASK})


class TestSensitivity(unittest.TestCase):
    def setUp(self):
        self.sim = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'),
                               tick=_d('0.01'))

    def test_h1_taker_side(self):
        """H1: public sell заполняет наш resting buy на bid."""
        s = self.sim
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        self.assertEqual(len(s.fills), 1)
        self.assertEqual(s.fills[0].side, 'buy')
        self.assertEqual(s.fills[0].price, _d('1.05'))

    def test_h2_inversion(self):
        """H2: инверсия — public buy заполняет наш resting buy."""
        s = Sensitivity('PRLUSDT', 'H2', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(2000.0))
        s.on_trade(2010.0, _d('1.05'), _d('100'), 'buy', book(2010.0))
        self.assertEqual(len(s.fills), 1)
        self.assertEqual(s.fills[0].side, 'buy')
        # а sell-трейд НЕ заполняет (инверсия)
        s2 = Sensitivity('PRLUSDT', 'H2', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s2.on_book(book(2000.0))
        s2.on_trade(2010.0, _d('1.05'), _d('100'), 'sell', book(2010.0))
        self.assertEqual(len(s2.fills), 0)

    def test_fifo_queue_ahead(self):
        """Очередь 1x: объём впереди гасится первым, наш филл — остаток."""
        s = Sensitivity('PRLUSDT', 'H1', '1x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        # очередь = видимый объём (100) * factor 1 = 100; маркет 150 -> нам 50
        s.on_trade(1010.0, _d('1.05'), _d('150'), 'sell', book(1010.0))
        self.assertEqual(len(s.fills), 1)
        self.assertAlmostEqual(float(s.fills[0].qty), 4.761905, places=4)

    def test_queue_blocked(self):
        """Очередь 2x: маркет-объём меньше очереди -> филла нет."""
        s = Sensitivity('PRLUSDT', 'H1', '2x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        self.assertEqual(len(s.fills), 0)

    def test_partial_fill_carries(self):
        """Частичное исполнение: остаток заявки остаётся, доливается позже."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('25'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('2'), 'sell', book(1010.0))  # частично
        self.assertEqual(len(s.fills), 1)
        self.assertTrue(s.fills[0].partial)
        self.assertIsNotNone(s.order)          # остаток ещё в очереди
        s.on_trade(1020.0, _d('1.05'), _d('100'), 'sell', book(1020.0))  # долив
        self.assertEqual(len(s.fills), 2)
        self.assertIsNone(s.order)             # entry закрыта
        self.assertIsNotNone(s.exit_order)

    def test_capital_limit_no_borrow(self):
        """Без займа: размер заявки > свободного капитала -> усечение."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('100'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        self.assertIsNotNone(s.order)
        # при 100 USDT бюджета и cash=50 не можем купить на полный номинал
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        self.assertEqual(len(s.fills), 1)
        self.assertGreaterEqual(float(s.cash), 0.0)  # не ушли в минус
        # база = 50 USDT стартовой + купленное на остаток (cash>0 исчерпан)
        self.assertGreater(float(s.base), 47.39)     # купили сверх стартовой

    def test_fee_applied(self):
        """Комиссия 0.1% с каждой стороны присутствует в fills."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        fee = float(s.fills[0].fee)
        self.assertAlmostEqual(fee, 5.0 * 0.001, places=4)

    def test_round_trip_pnl(self):
        """Полный цикл buy@1.05 -> sell@1.06 даёт положительный net_pnl."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        s.on_book(book(1011.0))
        s.on_trade(1015.0, _d('1.06'), _d('150'), 'buy', book(1015.0))
        r = s.finalize()
        self.assertEqual(r['n_fills'], 2)
        self.assertGreater(r['net_pnl'], 0.0)
        self.assertEqual(r['unrealized_qty'], 0.0)

    def test_force_exit_window_end(self):
        """Остаток на конец окна принудительно закрывается taker."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        # exit стоит на ask, но продаж не было — force по последнему стакану
        s.on_book(book(2000.0))
        s._force_exit(book(2000.0), 'window_end')
        r = s.finalize()
        self.assertTrue(any(f.taker for f in s.fills))

    def test_hold_timeout(self):
        """Удержание > 30 мин -> принудительный выход на on_book."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))
        s.on_trade(1010.0, _d('1.05'), _d('100'), 'sell', book(1010.0))
        s.on_book(book(1000.0 + 31 * 60))       # прошло 31 минута
        self.assertTrue(any(f.taker for f in s.fills))

    def test_delay_activation(self):
        """Задержка активации: сделка раньше срока не исполняет заявку."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 5.0, _d('5'), tick=_d('0.01'))
        s.on_book(book(1000.0))                  # активна с 1005
        s.on_trade(1002.0, _d('1.05'), _d('100'), 'sell', book(1002.0))
        self.assertEqual(len(s.fills), 0)        # ещё не активна
        s.on_trade(1006.0, _d('1.05'), _d('100'), 'sell', book(1006.0))
        self.assertEqual(len(s.fills), 1)        # теперь активна

    def test_no_lookahead_stale_book(self):
        """Запрет look-ahead: стакан старше STALE_S помечает unobserved."""
        s = Sensitivity('PRLUSDT', 'H1', '0x', 0.0, _d('5'), tick=_d('0.01'))
        old = book(1000.0)
        s.on_book(old)
        # трейд через 60с после известного стакана (STALE_S=30): unobserved
        s.on_trade(1060.0, _d('1.05'), _d('100'), 'sell', old)
        self.assertEqual(len(s.fills), 0)
        self.assertEqual(s.skipped_stale, 1)
        # свежий (возраст 5с) — исполняется
        s.on_trade(1005.0, _d('1.05'), _d('100'), 'sell', old)
        self.assertEqual(len(s.fills), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)