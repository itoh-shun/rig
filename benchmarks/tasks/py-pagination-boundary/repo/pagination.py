def paginate(items, page_number, page_size):
    start = (page_number - 1) * page_size
    return items[start : start + page_size]
