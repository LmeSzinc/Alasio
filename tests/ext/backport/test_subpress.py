import pytest

from alasio.ext.backport.subpress import _BaseExceptionGroup, suppress_keyboard_interrupt


class TestSuppressKeyboardInterrupt:
    def test_normal_execution(self):
        """
        Test: Normal execution without any exceptions.
        """
        called = False

        def callback():
            nonlocal called
            called = True

        with suppress_keyboard_interrupt(callback=callback):
            pass

        assert not called

    def test_suppress_keyboard_interrupt(self):
        """
        Test: Suppress a direct KeyboardInterrupt and execute the callback.
        """
        called = False

        def callback():
            nonlocal called
            called = True

        with suppress_keyboard_interrupt(callback=callback):
            raise KeyboardInterrupt

        assert called

    def test_no_suppress_other_exception(self):
        """
        Test: Do not suppress exceptions other than KeyboardInterrupt.
        """
        called = False

        def callback():
            nonlocal called
            called = True

        with pytest.raises(ValueError):
            with suppress_keyboard_interrupt(callback=callback):
                raise ValueError("test")

        assert not called

    def test_exception_group_only_ki(self):
        """
        Test: Suppress an exception group that only contains KeyboardInterrupt.
        """
        # This test only makes sense if _BaseExceptionGroup is a real ExceptionGroup
        if _BaseExceptionGroup is BaseException:
            pytest.skip("BaseExceptionGroup not available")

        called = False

        def callback():
            nonlocal called
            called = True

        with suppress_keyboard_interrupt(callback=callback):
            raise _BaseExceptionGroup("group", [KeyboardInterrupt()])

        assert called

    def test_exception_group_mixed(self):
        """
        Test: Partially suppress an exception group, re-raising other exceptions.
        """
        if _BaseExceptionGroup is BaseException:
            pytest.skip("BaseExceptionGroup not available")

        called = False

        def callback():
            nonlocal called
            called = True

        with pytest.raises((ValueError, _BaseExceptionGroup)) as excinfo:
            with suppress_keyboard_interrupt(callback=callback):
                # When ValueError is present, it should be re-raised
                raise _BaseExceptionGroup("group", [KeyboardInterrupt(), ValueError("test")])

        assert called
        # Verify ValueError is somewhere in the raised exception
        exc = excinfo.value
        if isinstance(exc, _BaseExceptionGroup):
            # Check if ValueError is in sub-exceptions
            assert any(isinstance(e, ValueError) for e in exc.exceptions)
        else:
            assert isinstance(exc, ValueError)

    def test_callback_keyboard_interrupt(self):
        """
        Test: Suppress KeyboardInterrupt raised within the callback itself.
        """
        called = 0

        def callback():
            nonlocal called
            called += 1
            raise KeyboardInterrupt

        with suppress_keyboard_interrupt(callback=callback):
            raise KeyboardInterrupt

        assert called == 1

    def test_callback_other_exception(self):
        """
        Test: Do not suppress other exceptions raised within the callback.
        """
        called = 0

        def callback():
            nonlocal called
            called += 1
            raise ValueError("callback error")

        with pytest.raises(ValueError) as excinfo:
            with suppress_keyboard_interrupt(callback=callback):
                raise KeyboardInterrupt

        assert called == 1
        assert "callback error" in str(excinfo.value)

    def test_callback_exception_group_ki(self):
        """
        Test: Suppress KeyboardInterrupt within an exception group in the callback.
        """
        if _BaseExceptionGroup is BaseException:
            pytest.skip("BaseExceptionGroup not available")

        called = 0

        def callback():
            nonlocal called
            called += 1
            raise _BaseExceptionGroup("callback group", [KeyboardInterrupt()])

        with suppress_keyboard_interrupt(callback=callback):
            raise KeyboardInterrupt

        assert called == 1

    def test_callback_exception_group_mixed(self):
        """
        Test: Re-raise other exceptions within an exception group in the callback.
        """
        if _BaseExceptionGroup is BaseException:
            pytest.skip("BaseExceptionGroup not available")

        called = 0

        def callback():
            nonlocal called
            called += 1
            raise _BaseExceptionGroup("callback group", [KeyboardInterrupt(), ValueError("callback error")])

        with pytest.raises((ValueError, _BaseExceptionGroup)) as excinfo:
            with suppress_keyboard_interrupt(callback=callback):
                raise KeyboardInterrupt

        assert called == 1
        exc = excinfo.value
        # In ExceptionGroup, the message "callback error" should be in one of the sub-exceptions
        if isinstance(exc, _BaseExceptionGroup):
            assert any("callback error" in str(e) for e in exc.exceptions)
        else:
            assert "callback error" in str(exc)

    def test_trio_nursery_ki(self):
        """
        Test: Suppress KeyboardInterrupt raised within a trio nursery.
        """
        import trio
        called = False

        def callback():
            nonlocal called
            called = True

        with suppress_keyboard_interrupt(callback=callback):
            async def raise_ki():
                raise KeyboardInterrupt

            async def main():
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(raise_ki)

            trio.run(main)

        assert called

    def test_trio_nursery_mixed(self):
        """
        Test: Partially suppress KeyboardInterrupt in a trio nursery with other exceptions.
        """
        import trio
        called = False

        def callback():
            nonlocal called
            called = True

        with pytest.raises((ValueError, _BaseExceptionGroup)) as excinfo:
            with suppress_keyboard_interrupt(callback=callback):
                async def raise_ki():
                    raise KeyboardInterrupt

                async def raise_ve():
                    raise ValueError("trio error")

                async def main():
                    async with trio.open_nursery() as nursery:
                        nursery.start_soon(raise_ki)
                        nursery.start_soon(raise_ve)

                trio.run(main)

        assert called
        exc = excinfo.value
        if isinstance(exc, _BaseExceptionGroup):
            assert any("trio error" in str(e) for e in exc.exceptions)
        else:
            assert "trio error" in str(exc)
