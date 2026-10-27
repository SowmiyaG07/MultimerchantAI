
from app.utilities.StateMapping import STATE_MAPPING

_VALID_STATE_CODES = set(STATE_MAPPING.values())

_DIRECTIONAL_STREET_SUFFIXES = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}

def _isvalidcode(value):
    """Check whether the value is a valid US state name or code."""
    if not value:
        return False
    text = value.strip().lower()
    return text in STATE_MAPPING or text.upper() in _VALID_STATE_CODES

def drop_invalid_state(state):
    """
    Drop a `state` argument that doesn't resolve to a real US state.

    Observed failure: "Which U.S. state is Dreamland BBQ located
    in?" -- the LLM mistook the question's own wording *about* the
    state field for a state *value*, producing
    tool_args={"name": "Dreamland BBQ", "state": "U.S.", ...}.
    normalize_state("U.S.") doesn't recognize it as invalid and
    just uppercases it unchanged, so `L.State LIKE '%U.S.%'` never
    matches any real 2-letter state code -- combined with AND, that
    silently zeroed out a name search that would otherwise have
    found the merchant.

    An invalid state can only ever narrow a search to zero rows (no
    real row's State column is "U.S."), so dropping it is always
    safe. `name` is accepted only so the log event/print statement
    can say which lookup this happened during.
    """
    if not state or _isvalidcode(state):
        return state
    
    return ""

def drop_state_derived_from_address(state, address):
    """
    Drop a `state` argument that's actually the trailing directional
    suffix of a street address, not an independent state value.

    Observed failure: "Is there a salon at 929 Peachtree St NE?" --
    the LLM correctly extracted address="929 Peachtree St NE" but
    ALSO set state="NE", reading the address's own USPS directional
    suffix (Northeast) as if it were the two-letter code for
    Nebraska. "NE" is validly formatted, so drop_invalid_state lets
    it through -- L.State LIKE '%NE%' then silently excluded the
    real merchant (State='GA'), which combined with AND meant a
    state the user never mentioned zeroed out an address search that
    would otherwise have found the row.

    Only triggers when the address's LAST word is exactly the state
    value (case-insensitive) -- never touches a state independently
    given alongside an address (e.g. "salon on Peachtree St in
    Georgia" keeps state="Georgia"/"GA", since "Georgia" isn't a
    directional suffix at all).
    
    """
    
    if not state or not address:
        return state
    
    address_token = address.strip().split()
    
    if not address_token:
        return state
    last_token = address_token[-1].upper().rstrip(".")
    
    if last_token not in _DIRECTIONAL_STREET_SUFFIXES:
        return state
    
    if last_token != state.strip().upper():
            return state
    
    return ""
    