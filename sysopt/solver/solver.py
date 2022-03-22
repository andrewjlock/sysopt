"""Methods and objects for solving system optimisation problems."""

import dataclasses
import weakref
from typing import Optional, Dict, List, Union, Iterable

from sysopt import symbolic
from sysopt.symbolic import DecisionVariable
from sysopt.solver.symbol_database import SymbolDatabase
from sysopt.block import Composite


@dataclasses.dataclass
class CandidateSolution:
    """A candidate solution to a constrained optimisation problem. """
    cost: float
    trajectory: object
    constraints: Optional[List[float]] = None


class SolverContext:
    """Context manager for model simulation and optimisation.

    Args:
        model:  The model block diagram

    """
    def __init__(self,
                 model: Composite,
                 t_final: Union[float, DecisionVariable],
                 constants: Optional[Dict] = None,
                 path_resolution: int = 50
                 ):
        self.model = model
        self.symbol_db = SymbolDatabase(t_final)
        self.start = self._time_point(0)
        self.end = self._time_point(t_final)
        self.t = self._time_point(self.symbol_db.t)
        self.constants = constants
        self.resolution = path_resolution

    @staticmethod
    def _time_point(value):
        if not symbolic.is_symbolic(value):
            assert isinstance(value, (float, int))
            obj = symbolic.constant(value)
        else:
            obj = value
        assert not hasattr(value, 'context'), \
            'Variable is associated with another problem context.'
        return obj

    def __enter__(self):
        for obj in (self.start, self.end, self.t):
            setattr(obj, 'context', self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for obj in (self.start, self.end, self.t):
            delattr(obj, 'context')

    def get_symbolic_integrator(self,
                                decision_variables: Iterable[DecisionVariable]):
        integrator = self.get_integrator()
        parameter_arguments = {}

        for dv in decision_variables:
            if dv is not self.symbol_db.t_final:
                block, slc = dv.parameter

                if slc.stop - slc.start == 1:

                    parameter_arguments[block.parameters[slc.start]] = dv
                else:
                    iterator = range(
                        slc.start, slc.stop, slc.step if slc.step else 1
                    )
                    for i in iterator:
                        parameter_arguments[block.parameters[i]] = dv[i]
        parameters = symbolic.concatenate(
            *[parameter_arguments[p] if p in parameter_arguments
              else self.constants[p] for p in self.model.parameters]
        )

        symbolic_evaluation = integrator(self.t, parameters)

        f = symbolic.lambdify(
            symbolic_evaluation, [self.t,
                                  symbolic.concatenate(decision_variables)]
        )
        return f

    def _prepare_path(self, decision_variables):
        t_final = self.symbol_db.t_final
        parameters = self.constants.copy()

        for dv in decision_variables:
            if dv is self.symbol_db.t_final:
                t_final = float(decision_variables[self.symbol_db.t_final])
            else:
                block, slc = dv.parameter
                values = decision_variables[dv]

                if slc.stop - slc.start == 1:
                    parameters[block.parameters[slc.start]] = float(values)
                else:
                    iterator = range(
                        slc.start, slc.stop, slc.step if slc.step else 1
                    )
                    for i in iterator:
                        parameters[block.parameters[i]] = float(values[i])

        assert not symbolic.is_symbolic(t_final), 'Must specify a final time'
        integrator = self.get_integrator(self.resolution)

        params = [float(parameters[p]) for p in self.model.parameters]
        func = integrator(t_final, params)

        return func, t_final

    def evaluate(self, problem,
                 decision_variables: Dict[DecisionVariable, float]):

        y_symbols = self.symbol_db.get_or_create_outputs(self.model)
        y, t_final = self._prepare_path(decision_variables)

        point_values = []
        point_symbols = []
        for s, t, expr in self.symbol_db.expressions:
            point_symbols.append(s)
            if not isinstance(t, float):
                if t is self.symbol_db.t_final:
                    point_values += [expr(y(t_final))]
                else:
                    raise NotImplementedError(
                        f'Don\'t know how to evaluate time point {t}')
            else:
                point_values += [expr(y(t))]

        path_symbols, path_values = zip(*[
            (v, expr(y_symbols)) for v, expr in self.symbol_db.path_variables
        ])

        path_symbols = symbolic.concatenate(*path_symbols)
        path_values = symbolic.concatenate(*path_values)

        dv_symbols = list(decision_variables.keys())
        dv_symbols = symbolic.concatenate(*dv_symbols)
        dv_values = list(decision_variables.values())

        point_symbols = symbolic.concatenate(*point_symbols)
        point_values = symbolic.concatenate(*point_values)

        point_arguments = [y_symbols, dv_symbols, point_symbols]
        path_arguments = [y_symbols, dv_symbols, point_symbols, path_symbols]

        cost_function_symbolic = symbolic.lambdify(
            problem.cost, point_arguments, 'cost'
        )(y_symbols, dv_symbols, point_values)

        cost_function = symbolic.lambdify(
            cost_function_symbolic,
            [y_symbols, dv_symbols]
        )
        value = cost_function(y(t_final), dv_values)
        constraints = []
        for c in problem.constraints:
            if self.is_time_varying(c):
                constraint = symbolic.lambdify(c, path_arguments)(
                    y_symbols, dv_symbols, point_values, path_values
                )
                f_of_y = symbolic.lambdify(constraint, [y_symbols])
                constraints.append(
                    symbolic.sum_axis(f_of_y(y.x) - 1, 1)
                )
            else:
                constraint = symbolic.lambdify(c, point_arguments)(
                    y(t_final), dv_values, point_values)
                constraints.append(constraint - 1)

        return CandidateSolution(value, y, constraints)

    def solve(self, problem):
        pass

    def signal(self, parent, indices, t):
        assert parent.parent.parent is None, \
            'Can only create signals from root model'

        vector = self.symbol_db.get_or_create_port_variables(parent)

        matrix = symbolic.projection_matrix(
            list(enumerate(indices)), len(vector)
        )

        if t is self.symbol_db.t:
            return self.symbol_db.get_path_variable(
                matrix @ vector, vector)
        else:
            return self.symbol_db.get_point_variable(matrix @ vector, t, vector)

    def _get_parameter_vector(self):
        return [self.constants[p] for p in self.model.parameters]

    def integrate(self, parameters=None, t_final=None, resolution=50):

        if not parameters:
            parameters = self._get_parameter_vector()

        func = self.get_integrator(resolution)
        if not t_final:
            t_final = self.symbol_db.t_final
        return func(t_final, parameters)

    @property
    def flattened_system(self):
        return self.symbol_db.get_flattened_system(self.model)

    def get_integrator(self, resolution=50):
        return symbolic.Integrator(
            self.symbol_db.t,
            self.flattened_system,
            resolution=resolution
        )

    def is_time_varying(self, symbol_or_expression):

        symbols = symbolic.list_symbols(symbol_or_expression)

        if self.symbol_db.t in symbols:
            return True

        for s, _ in self.symbol_db.path_variables:
            if s in symbols:
                return True

        return False

    def problem(self, cost, arguments, subject_to=None):
        return Problem(self, cost, arguments, subject_to)


class Problem:
    """Optimisation Problem.

    Args:
        context:        Model context for this problem.
        cost:           Symbolic expression for cost function.
        arguments:      Decision variables/arguments for cost.
        constraints:    Path, terminal and parameter constraints for the
            problem.

    """

    def __init__(self,
                 context: SolverContext,
                 cost,
                 arguments: List,
                 constraints: Optional[List]):
        self._cost = cost
        self._context = weakref.ref(context)
        self.arguments = arguments
        self.constraints = constraints if constraints else []

    @property
    def context(self):
        return self._context()

    @property
    def cost(self):
        return self._cost

    def __call__(self, *args):
        """Evaluate the problem with the given arguments."""
        assert len(args) == len(self.arguments), \
            f'Invalid arguments: expected {self.arguments}, received {args}'
        arg_dict = dict(zip(self.arguments, args))

        return self.context.evaluate(self, arg_dict)
