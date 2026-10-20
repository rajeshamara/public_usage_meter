import datetime as dt
import tempfile
import unittest
from pathlib import Path
from usage_meter import budget, weekly, windows, Store, Client

class Tests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026,9,20,12).astimezone()
        self.reset = self.now.timestamp()+5*86400
    def test_missing_is_not_zero(self):
        self.assertIsNone(weekly({'rateLimits':{'primary':{'usedPercent':None}}}))
    def test_short_window_not_weekly(self):
        self.assertIsNone(weekly({'rateLimits':{'primary':{'usedPercent':0,'resetsAt':1,'windowDurationMins':300}}}))
    def test_first_day_excludes_historical_spend(self):
        rows=[(self.now.timestamp()-60,20,self.reset)]
        b=budget(rows,{'usedPercent':24,'resetsAt':self.reset},self.now)
        self.assertEqual((b['today'],b['available'],b['partial']),(4,10,True))
    def test_carryover_and_day_delta(self):
        yesterday=self.now-dt.timedelta(days=1)
        rows=[(yesterday.timestamp(),20,self.reset),(yesterday.timestamp()+3600,25,self.reset),(self.now.timestamp(),28,self.reset)]
        b=budget(rows,{'usedPercent':28,'resetsAt':self.reset},self.now)
        self.assertEqual((b['today'],b['available']),(3,20))
    def test_new_reset_does_not_carry_old_budget(self):
        rows=[(self.now.timestamp()-86400,90,self.reset-86400),(self.now.timestamp(),0,self.reset)]
        self.assertEqual(budget(rows,{'usedPercent':0,'resetsAt':self.reset},self.now)['available'],14)
    def test_cap_and_over_budget(self):
        rows=[(self.now.timestamp(),95,self.reset)]
        self.assertEqual(budget(rows,{'usedPercent':96,'resetsAt':self.reset},self.now)['available'],4)
        rows=[(self.now.timestamp(),0,self.reset)]
        self.assertEqual(budget(rows,{'usedPercent':20,'resetsAt':self.reset},self.now)['over'],6)
    def test_adjustment(self):
        rows=[(self.now.timestamp()-60,30,self.reset),(self.now.timestamp(),10,self.reset)]
        self.assertTrue(budget(rows,{'usedPercent':10,'resetsAt':self.reset},self.now)['decrease'])
    def test_reset_timestamp_jitter_preserves_budget(self):
        rows=[(self.now.timestamp()-60,0,self.reset),(self.now.timestamp(),1,self.reset+1)]
        b=budget(rows,{'usedPercent':1,'resetsAt':self.reset+1},self.now)
        self.assertEqual((b['today'],b['available']),(1,13))

    def test_model_calls_rejected(self):
        client=Client.__new__(Client)
        with self.assertRaises(ValueError):client.call('turn/start')
        with self.assertRaises(ValueError):client.call('account/rateLimitResetCredit/consume')

if __name__=='__main__':unittest.main()
