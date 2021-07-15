import string

# MODEL FUNCTIONS
def build_english_list(items):
  result = ''

  if len(items) == 0:
    return result

  if len(items) == 1:
    return items[0]

  if len(items) == 2:
    return items[0] + " and " + items[1]

  for i, item in enumerate(items):
    item = item.strip()

    # If last one.
    if i + 1 == len(items):
      result += "and " + item
    else:
      result += item + ", "

  return result


def cap_first_word(arg):
  if type(arg) == str:
    s = arg.strip()
    return s[0].upper() + s[1:None]

  if type(arg) == list:
    return [string.capwords(arg[0])] + arg[1:None]

  raise TypeError("Invalid type passed to function: " + type(arg))


def last_char(s):
  return s[len(s) - 1]


def ends_with_period(s):
  return bool(s[-1:] == ".")
