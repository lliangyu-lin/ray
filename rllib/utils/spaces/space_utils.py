import gymnasium as gym
from gymnasium.spaces import Tuple, Dict
from gymnasium.core import ActType, ObsType
import numpy as np
from ray.rllib.utils.annotations import DeveloperAPI
import tree  # pip install dm_tree
from typing import Any, List, Optional, Union


@DeveloperAPI
class BatchedNdArray(np.ndarray):
    """A ndarray-wrapper the usage of which indicates that there a batch dim exists.

    This is such that our `batch()` utility can distinguish between having to
    stack n individual batch items (each one w/o any batch dim) vs having to
    concatenate n already batched items (each one possibly with a different batch
    dim, but definitely with some batch dim).

    TODO (sven): Maybe replace this by a list-override instead.
    """

    def __new__(cls, input_array):
        # Use __new__ to create a new instance of our subclass.
        obj = np.asarray(input_array).view(cls)
        return obj


@DeveloperAPI
def get_original_space(space: gym.Space) -> gym.Space:
    """Returns the original space of a space, if any.

    This function recursively traverses the given space and returns the original space
    at the very end of the chain.

    Args:
        space: The space to get the original space for.

    Returns:
        The original space or the given space itself if no original space is found.
    """
    if hasattr(space, "original_space"):
        return get_original_space(space.original_space)
    else:
        return space


@DeveloperAPI
def is_composite_space(space: gym.Space) -> bool:
    """Returns true, if the space is composite.

    Note, we follow here the glossary of `gymnasium` by which any spoace
    that holds other spaces is defined as being 'composite'.

    Args:
        space: The space to be checked for being composed of other spaces.

    Returns:
        True, if the space is composed of other spaces, otherwise False.
    """
    if type(space) in [
        gym.spaces.Dict,
        gym.spaces.Graph,
        gym.spaces.Sequence,
        gym.spaces.Tuple,
    ]:
        return True
    else:
        return False


@DeveloperAPI
def to_jsonable_if_needed(
    sample: Union[ActType, ObsType], space: gym.Space
) -> Union[ActType, ObsType, List]:
    """Returns a jsonabled space sample, if the space is composite.

    Checks, if the space is composite and converts the sample to a jsonable
    struct in this case. Otherwise return the sample as is.

    Args:
        sample: Any action or observation type possible in `gymnasium`.
        space: Any space defined in `gymnasium.spaces`.

    Returns:
        The `sample` as-is, if the `space` is composite, otherwise converts the
        composite sample to a JSONable data type.
    """

    if is_composite_space(space):
        return space.to_jsonable([sample])
    else:
        return sample


@DeveloperAPI
def from_jsonable_if_needed(
    sample: Union[ActType, ObsType], space: gym.Space
) -> Union[ActType, ObsType, List]:
    """Returns a jsonabled space sample, if the space is composite.

    Checks, if the space is composite and converts the sample to a JSONable
    struct in this case. Otherwise return the sample as is.

    Args:
        sample: Any action or observation type possible in `gymnasium`, or a
            JSONable data type.
        space: Any space defined in `gymnasium.spaces`.

    Returns:
        The `sample` as-is, if the `space` is not composite, otherwise converts the
        composite sample jsonable to an actual `space` sample..
    """

    if is_composite_space(space):
        return space.from_jsonable(sample)[0]
    else:
        return sample


@DeveloperAPI
def flatten_space(space: gym.Space) -> List[gym.Space]:
    """Flattens a gym.Space into its primitive components.

    Primitive components are any non Tuple/Dict spaces.

    Args:
        space: The gym.Space to flatten. This may be any
            supported type (including nested Tuples and Dicts).

    Returns:
        List[gym.Space]: The flattened list of primitive Spaces. This list
            does not contain Tuples or Dicts anymore.
    """

    def _helper_flatten(space_, return_list):
        from ray.rllib.utils.spaces.flexdict import FlexDict

        if isinstance(space_, Tuple):
            for s in space_:
                _helper_flatten(s, return_list)
        elif isinstance(space_, (Dict, FlexDict)):
            for k in sorted(space_.spaces):
                _helper_flatten(space_[k], return_list)
        else:
            return_list.append(space_)

    ret = []
    _helper_flatten(space, ret)
    return ret


@DeveloperAPI
def get_base_struct_from_space(space):
    """Returns a Tuple/Dict Space as native (equally structured) py tuple/dict.

    Args:
        space: The Space to get the python struct for.

    Returns:
        Union[dict,tuple,gym.Space]: The struct equivalent to the given Space.
            Note that the returned struct still contains all original
            "primitive" Spaces (e.g. Box, Discrete).

    .. testcode::
        :skipif: True

        get_base_struct_from_space(Dict({
            "a": Box(),
            "b": Tuple([Discrete(2), Discrete(3)])
        }))

    .. testoutput::

        dict(a=Box(), b=tuple(Discrete(2), Discrete(3)))
    """

    def _helper_struct(space_):
        if isinstance(space_, Tuple):
            return tuple(_helper_struct(s) for s in space_)
        elif isinstance(space_, Dict):
            return {k: _helper_struct(space_[k]) for k in space_.spaces}
        else:
            return space_

    return _helper_struct(space)


@DeveloperAPI
def get_dummy_batch_for_space(
    space: gym.Space,
    batch_size: int = 32,
    *,
    fill_value: Union[float, int, str] = 0.0,
    time_size: Optional[int] = None,
    time_major: bool = False,
    one_hot_discrete: bool = False,
) -> np.ndarray:
    """Returns batched dummy data (using `batch_size`) for the given `space`.

    Note: The returned batch will not pass a `space.contains(batch)` test
    as an additional batch dimension has to be added at axis 0, unless `batch_size` is
    set to 0.

    Args:
        space: The space to get a dummy batch for.
        batch_size: The required batch size (B). Note that this can also
            be 0 (only if `time_size` is None!), which will result in a
            non-batched sample for the given space (no batch dim).
        fill_value: The value to fill the batch with
            or "random" for random values.
        time_size: If not None, add an optional time axis
            of `time_size` size to the returned batch. This time axis might either
            be inserted at axis=1 (default) or axis=0, if `time_major` is True.
        time_major: If True AND `time_size` is not None, return batch
            as shape [T x B x ...], otherwise as [B x T x ...]. If `time_size`
            if None, ignore this setting and return [B x ...].
        one_hot_discrete: If True, will return one-hot vectors (instead of
            int-values) for those sub-components of a (possibly complex) `space`
            that are Discrete or MultiDiscrete. Note that in case `fill_value` is 0.0,
            this will result in zero-hot vectors (where all slots have a value of 0.0).

    Returns:
        The dummy batch of size `bqtch_size` matching the given space.
    """
    # Complex spaces. Perform recursive calls of this function.
    if isinstance(space, (gym.spaces.Dict, gym.spaces.Tuple, dict, tuple)):
        base_struct = space
        if isinstance(space, (gym.spaces.Dict, gym.spaces.Tuple)):
            base_struct = get_base_struct_from_space(space)
        return tree.map_structure(
            lambda s: get_dummy_batch_for_space(
                space=s,
                batch_size=batch_size,
                fill_value=fill_value,
                time_size=time_size,
                time_major=time_major,
                one_hot_discrete=one_hot_discrete,
            ),
            base_struct,
        )

    if one_hot_discrete:
        if isinstance(space, gym.spaces.Discrete):
            space = gym.spaces.Box(0.0, 1.0, (space.n,), np.float32)
        elif isinstance(space, gym.spaces.MultiDiscrete):
            space = gym.spaces.Box(0.0, 1.0, (np.sum(space.nvec),), np.float32)

    # Primivite spaces: Box, Discrete, MultiDiscrete.
    # Random values: Use gym's sample() method.
    if fill_value == "random":
        if time_size is not None:
            assert batch_size > 0 and time_size > 0
            if time_major:
                return np.array(
                    [
                        [space.sample() for _ in range(batch_size)]
                        for t in range(time_size)
                    ],
                    dtype=space.dtype,
                )
            else:
                return np.array(
                    [
                        [space.sample() for t in range(time_size)]
                        for _ in range(batch_size)
                    ],
                    dtype=space.dtype,
                )
        else:
            return np.array(
                [space.sample() for _ in range(batch_size)]
                if batch_size > 0
                else space.sample(),
                dtype=space.dtype,
            )
    # Fill value given: Use np.full.
    else:
        if time_size is not None:
            assert batch_size > 0 and time_size > 0
            if time_major:
                shape = [time_size, batch_size]
            else:
                shape = [batch_size, time_size]
        else:
            shape = [batch_size] if batch_size > 0 else []
        return np.full(
            shape + list(space.shape), fill_value=fill_value, dtype=space.dtype
        )


@DeveloperAPI
def flatten_to_single_ndarray(input_):
    """Returns a single np.ndarray given a list/tuple of np.ndarrays.

    Args:
        input_ (Union[List[np.ndarray], np.ndarray]): The list of ndarrays or
            a single ndarray.

    Returns:
        np.ndarray: The result after concatenating all single arrays in input_.

    .. testcode::
        :skipif: True

        flatten_to_single_ndarray([
            np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
            np.array([7, 8, 9]),
        ])

    .. testoutput::

        np.array([
            1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0
        ])
    """
    # Concatenate complex inputs.
    if isinstance(input_, (list, tuple, dict)):
        expanded = []
        for in_ in tree.flatten(input_):
            expanded.append(np.reshape(in_, [-1]))
        input_ = np.concatenate(expanded, axis=0).flatten()
    return input_


@DeveloperAPI
def batch(
    list_of_structs: List[Any],
    *,
    individual_items_already_have_batch_dim: Union[bool, str] = False,
):
    """Converts input from a list of (nested) structs to a (nested) struct of batches.

    Input: List of structs (each of these structs representing a single batch item).
        [
            {"a": 1, "b": (4, 7.0)},  <- batch item 1
            {"a": 2, "b": (5, 8.0)},  <- batch item 2
            {"a": 3, "b": (6, 9.0)},  <- batch item 3
        ]

    Output: Struct of different batches (each batch has size=3 b/c there were 3 items
        in the original list):
        {
            "a": np.array([1, 2, 3]),
            "b": (np.array([4, 5, 6]), np.array([7.0, 8.0, 9.0]))
        }

    Args:
        list_of_structs: The list of (possibly nested) structs. Each item
            in this list represents a single batch item.
        individual_items_already_have_batch_dim: True, if the individual items in
            `list_of_structs` already have a batch dim. In this case, we will
            concatenate (instead of stack) at the end. In the example above, this would
            look like this: Input: [{"a": [1], "b": ([4], [7.0])}, ...] -> Output: same
            as in above example.
            If the special value "auto" is used,

    Returns:
        The struct of component batches. Each leaf item in this struct represents the
        batch for a single component (in case struct is tuple/dict). If the input is a
        simple list of primitive items, e.g. a list of floats, a np.array of floats
        will be returned.
    """
    if not list_of_structs:
        raise ValueError("Input `list_of_structs` does not contain any items.")

    # TODO (sven): Maybe replace this by a list-override (usage of which indicated
    #  this method that concatenate should be used (not stack)).
    if individual_items_already_have_batch_dim == "auto":
        flat = tree.flatten(list_of_structs[0])
        individual_items_already_have_batch_dim = isinstance(flat[0], BatchedNdArray)

    np_func = np.concatenate if individual_items_already_have_batch_dim else np.stack
    ret = tree.map_structure(
        lambda *s: np.ascontiguousarray(np_func(s, axis=0)), *list_of_structs
    )
    return ret


@DeveloperAPI
def unbatch(batches_struct):
    """Converts input from (nested) struct of batches to batch of structs.

    Input: Struct of different batches (each batch has size=3):
        {
            "a": np.array([1, 2, 3]),
            "b": (np.array([4, 5, 6]), np.array([7.0, 8.0, 9.0]))
        }
    Output: Batch (list) of structs (each of these structs representing a
        single action):
        [
            {"a": 1, "b": (4, 7.0)},  <- action 1
            {"a": 2, "b": (5, 8.0)},  <- action 2
            {"a": 3, "b": (6, 9.0)},  <- action 3
        ]

    Args:
        batches_struct: The struct of component batches. Each leaf item
            in this struct represents the batch for a single component
            (in case struct is tuple/dict).
            Alternatively, `batches_struct` may also simply be a batch of
            primitives (non tuple/dict).

    Returns:
        The list of individual structs. Each item in the returned list represents a
        single (maybe complex) batch item.
    """
    flat_batches = tree.flatten(batches_struct)

    out = []
    for batch_pos in range(len(flat_batches[0])):
        out.append(
            tree.unflatten_as(
                batches_struct,
                [flat_batches[i][batch_pos] for i in range(len(flat_batches))],
            )
        )
    return out


@DeveloperAPI
def clip_action(action, action_space):
    """Clips all components in `action` according to the given Space.

    Only applies to Box components within the action space.

    Args:
        action: The action to be clipped. This could be any complex
            action, e.g. a dict or tuple.
        action_space: The action space struct,
            e.g. `{"a": Distrete(2)}` for a space: Dict({"a": Discrete(2)}).

    Returns:
        Any: The input action, but clipped by value according to the space's
            bounds.
    """

    def map_(a, s):
        if isinstance(s, gym.spaces.Box):
            a = np.clip(a, s.low, s.high)
        return a

    return tree.map_structure(map_, action, action_space)


@DeveloperAPI
def unsquash_action(action, action_space_struct):
    """Unsquashes all components in `action` according to the given Space.

    Inverse of `normalize_action()`. Useful for mapping policy action
    outputs (normalized between -1.0 and 1.0) to an env's action space.
    Unsquashing results in cont. action component values between the
    given Space's bounds (`low` and `high`). This only applies to Box
    components within the action space, whose dtype is float32 or float64.

    Args:
        action: The action to be unsquashed. This could be any complex
            action, e.g. a dict or tuple.
        action_space_struct: The action space struct,
            e.g. `{"a": Box()}` for a space: Dict({"a": Box()}).

    Returns:
        Any: The input action, but unsquashed, according to the space's
            bounds. An unsquashed action is ready to be sent to the
            environment (`BaseEnv.send_actions([unsquashed actions])`).
    """

    def map_(a, s):
        if (
            isinstance(s, gym.spaces.Box)
            and np.all(s.bounded_below)
            and np.all(s.bounded_above)
        ):
            if s.dtype == np.float32 or s.dtype == np.float64:
                # Assuming values are roughly between -1.0 and 1.0 ->
                # unsquash them to the given bounds.
                a = s.low + (a + 1.0) * (s.high - s.low) / 2.0
                # Clip to given bounds, just in case the squashed values were
                # outside [-1.0, 1.0].
                a = np.clip(a, s.low, s.high)
            elif np.issubdtype(s.dtype, np.integer):
                # For Categorical and MultiCategorical actions, shift the selection
                # into the proper range.
                a = s.low + a
        return a

    return tree.map_structure(map_, action, action_space_struct)


@DeveloperAPI
def normalize_action(action, action_space_struct):
    """Normalizes all (Box) components in `action` to be in [-1.0, 1.0].

    Inverse of `unsquash_action()`. Useful for mapping an env's action
    (arbitrary bounded values) to a [-1.0, 1.0] interval.
    This only applies to Box components within the action space, whose
    dtype is float32 or float64.

    Args:
        action: The action to be normalized. This could be any complex
            action, e.g. a dict or tuple.
        action_space_struct: The action space struct,
            e.g. `{"a": Box()}` for a space: Dict({"a": Box()}).

    Returns:
        Any: The input action, but normalized, according to the space's
            bounds.
    """

    def map_(a, s):
        if isinstance(s, gym.spaces.Box) and (
            s.dtype == np.float32 or s.dtype == np.float64
        ):
            # Normalize values to be exactly between -1.0 and 1.0.
            a = ((a - s.low) * 2.0) / (s.high - s.low) - 1.0
        return a

    return tree.map_structure(map_, action, action_space_struct)


@DeveloperAPI
def convert_element_to_space_type(element: Any, sampled_element: Any) -> Any:
    """Convert all the components of the element to match the space dtypes.

    Args:
        element: The element to be converted.
        sampled_element: An element sampled from a space to be matched
            to.

    Returns:
        The input element, but with all its components converted to match
        the space dtypes.
    """

    def map_(elem, s):
        if isinstance(s, np.ndarray):
            if not isinstance(elem, np.ndarray):
                assert isinstance(
                    elem, (float, int)
                ), f"ERROR: `elem` ({elem}) must be np.array, float or int!"
                if s.shape == ():
                    elem = np.array(elem, dtype=s.dtype)
                else:
                    raise ValueError(
                        "Element should be of type np.ndarray but is instead of \
                            type {}".format(
                            type(elem)
                        )
                    )
            elif s.dtype != elem.dtype:
                elem = elem.astype(s.dtype)

        # Gymnasium now uses np.int_64 as the dtype of a Discrete action space
        elif isinstance(s, int) or isinstance(s, np.int_):
            if isinstance(elem, float) and elem.is_integer():
                elem = int(elem)
            # Note: This does not check if the float element is actually an integer
            if isinstance(elem, np.float_):
                elem = np.int64(elem)

        return elem

    return tree.map_structure(map_, element, sampled_element, check_types=False)
