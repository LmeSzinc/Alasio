from unittest.mock import patch

from alasio.base.timer import Timer


class TestTimerInitialization:
    """Test Timer initialization"""

    def test_init_basic(self):
        """Test basic initialization"""
        t = Timer(limit=5.0, count=10)
        assert t.limit == 5.0
        assert t.count == 10
        assert t._start == 0.0
        assert t._access == 0

    def test_init_default_count(self):
        """Test default count value"""
        t = Timer(limit=3.0)
        assert t.limit == 3.0
        assert t.count == 0

    def test_from_seconds(self):
        """Test from_seconds class method"""
        t = Timer.from_seconds(limit=10, speed=0.5)
        assert t.limit == 10
        assert t.count == 20  # 10 / 0.5 = 20

    def test_from_seconds_slow_device(self):
        """Test slow device scenario"""
        t = Timer.from_seconds(limit=5, speed=1.0)
        assert t.limit == 5
        assert t.count == 5  # 5 / 1.0 = 5


class TestTimerStart:
    """Test Timer start functionality"""

    def test_start(self):
        """Test starting the timer"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.return_value = 100.0
            t = Timer(limit=5.0, count=10)

            assert not t.started()
            t.start()
            assert t.started()
            assert t._start == 100.0
            assert t._access == 0

    def test_start_multiple_times(self):
        """Test multiple starts don't reset the timer"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 105.0]
            t = Timer(limit=5.0, count=10)

            t.start()
            first_start = t._start
            t.start()  # Second start should not change _start
            assert t._start == first_start

    def test_started_before_start(self):
        """Test started() returns False before starting"""
        t = Timer(limit=5.0)
        assert not t.started()


class TestTimerCurrent:
    """Test current time and count methods"""

    def test_current_time_not_started(self):
        """Test current time when not started"""
        t = Timer(limit=5.0)
        assert t.current_time() == 0.0

    def test_current_time_after_start(self):
        """Test current time after starting"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 103.5]
            t = Timer(limit=5.0)

            t.start()
            elapsed = t.current_time()
            assert elapsed == 3.5

    def test_current_time_negative_protection(self):
        """Test protection against time going backwards"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 95.0]  # Time goes backwards
            t = Timer(limit=5.0)

            t.start()
            elapsed = t.current_time()
            assert elapsed == 0.0  # Should return 0 instead of negative

    def test_current_count(self):
        """Test current access count"""
        t = Timer(limit=5.0, count=10)
        assert t.current_count() == 0

        t.add_count()
        assert t.current_count() == 1

        t.add_count()
        assert t.current_count() == 2


class TestTimerReached:
    """Test reached() method"""

    def test_reached_not_started(self):
        """Test reached() returns True when not started (fast first try)"""
        t = Timer(limit=5.0, count=10)
        assert t.reached()

    def test_reached_time_not_enough(self):
        """Test returns False when time is insufficient"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 102.0]  # Only 2 seconds elapsed
            t = Timer(limit=5.0, count=10)

            t.start()
            # Simulate 11 accesses (exceeds count)
            for _ in range(10):
                t._access += 1

            assert not t.reached()  # Time not enough

    def test_reached_count_not_enough(self):
        """Test returns False when access count is insufficient"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 106.0]  # 6 seconds elapsed
            t = Timer(limit=5.0, count=10)

            t.start()
            # Only 5 accesses
            for _ in range(5):
                t._access += 1

            assert not t.reached()  # Count not enough

    def test_reached_both_conditions_met(self):
        """Test returns True when both time and count conditions are met"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 106.0]  # 6 seconds elapsed
            t = Timer(limit=5.0, count=10)

            t.start()
            # 10 accesses
            for _ in range(10):
                t._access += 1

            assert t.reached()  # Both time and count are sufficient

    def test_reached_increments_access(self):
        """Test each reached() call increments access count"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.return_value = 100.0
            t = Timer(limit=5.0, count=10)

            t.start()
            initial_access = t._access
            t.reached()
            assert t._access == initial_access + 1


class TestTimerReset:
    """Test reset functionality"""

    def test_reset(self):
        """Test reset() resets the timer"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 150.0]
            t = Timer(limit=5.0, count=10)

            t.start()
            t._access = 5

            t.reset()
            assert t._start == 150.0
            assert t._access == 0

    def test_clear(self):
        """Test clear() clears the timer"""
        t = Timer(limit=5.0, count=10)
        t._start = 100.0
        t._access = 5

        t.clear()
        assert t._start == 0.0
        assert t._access == 10  # Set to count value

    def test_reached_and_reset_true(self):
        """Test reached_and_reset() when condition is met"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 106.0, 106.0]
            t = Timer(limit=5.0, count=10)

            t.start()
            t._access = 10

            result = t.reached_and_reset()
            assert result is True
            assert t._access == 0  # Should be reset

    def test_reached_and_reset_false(self):
        """Test reached_and_reset() when condition is not met"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 102.0]
            t = Timer(limit=5.0, count=10)

            t.start()
            t._access = 5

            result = t.reached_and_reset()
            assert result is False
            assert t._access == 6  # Only incremented once (by reached() call)


class TestTimerWait:
    """Test wait() method"""

    def test_wait_with_remaining_time(self):
        """Test waiting when there is remaining time"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.sleep') as mock_sleep, \
                patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 102.0]  # At start and at wait
            t = Timer(limit=5.0, count=10)

            t.start()
            t.wait()

            # Should wait for 5 - 2 = 3 seconds
            mock_sleep.assert_called_once()
            assert abs(mock_sleep.call_args[0][0] - 3.0) < 0.001

    def test_wait_with_no_remaining_time(self):
        """Test no waiting when time has already expired"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.sleep') as mock_sleep, \
                patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 106.0]  # Already timed out
            t = Timer(limit=5.0, count=10)

            t.start()
            t.wait()

            # Should not call sleep
            mock_sleep.assert_not_called()


class TestTimerIntegration:
    """Integration tests"""

    def test_typical_usage_loop(self):
        """Test typical loop usage scenario"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            # Simulate time sequence
            times = [100.0, 101.0, 102.0, 103.0, 106.0, 107.0]
            mock_time.side_effect = times

            t = Timer(limit=5.0, count=3)
            execution_count = 0

            # Simulate loop
            for i in range(3):
                if t.reached():
                    execution_count += 1
                    t.reset()

            # First time: not started, returns True, executes
            # Second time: started but not reached, doesn't execute
            # Third time: conditions met, executes
            assert execution_count >= 1

    def test_chaining_methods(self):
        """Test method chaining"""
        timer_module = Timer.__module__

        with patch(f'{timer_module}.time') as mock_time:
            mock_time.side_effect = [100.0, 100.0]
            t = Timer(limit=5.0, count=10)

            result = t.start().add_count().add_count()
            assert result is t  # Ensure returns self
            assert t._access == 2

    def test_str_representation(self):
        """Test string representation"""
        t = Timer(limit=5.0, count=10)
        t._access = 3

        string_repr = str(t)
        assert 'Timer' in string_repr
        assert '5' in string_repr  # limit
        assert '3' in string_repr  # access count
        assert '10' in string_repr  # count
