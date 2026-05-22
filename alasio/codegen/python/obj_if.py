from alasio.codegen.python.obj_closure import ClosureObject
from alasio.codegen.python.obj_simple import *


class If(ClosureObject):
    """
    Define an if block.
    if {condition}:
        # content

    Examples:
        with gen.If('x > 0'):
            gen.Var('result', 'positive')
        # Output
        if x > 0:
            result = 'positive'
    """

    def __init__(self, gen, condition):
        super().__init__(gen)
        self.condition = condition

    def generate(self):
        yield f'{self.indent_str}if {self.condition}:'
        if self.items:
            yield from self.generate_items()
        else:
            with self:
                yield from Pass(self.gen).generate()


class Elif(ClosureObject):
    """
    Define an elif block.
    elif {condition}:
        # content

    Examples:
        with gen.Elif('x == 0'):
            gen.Var('result', 'zero')
        # Output
        elif x == 0:
            result = 'zero'
    """

    def __init__(self, gen, condition):
        super().__init__(gen)
        self.condition = condition

    def generate(self):
        yield f'{self.indent_str}elif {self.condition}:'
        if self.items:
            yield from self.generate_items()
        else:
            with self:
                yield from Pass(self.gen).generate()


class Else(ClosureObject):
    """
    Define an else block.
    else:
        # content

    Examples:
        with gen.Else():
            gen.Var('result', 'negative')
        # Output
        else:
            result = 'negative'
    """

    def generate(self):
        yield f'{self.indent_str}else:'
        if self.items:
            yield from self.generate_items()
        else:
            with self:
                yield from Pass(self.gen).generate()
