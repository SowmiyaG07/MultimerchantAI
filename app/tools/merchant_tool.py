from langchain.tools import tool


@tool
def searchmerchant(query: str) -> str:
    """ search merchant by location, category , name"""

    merchantlist = [{
        "name": "dreamlandbbq",
        "category": "restaurnat",
        "city": "sandiego",
        "state": "Alabama",
        "country": "United states"
    },

        {
        "name": "Anandhabavan",
        "category": "restaurnat",
        "city": "santon",
        "state": "newyork",
        "country": "United states"

    },
        {
        "name": "Alasaka",
        "category": "mobileservie",
        "city": "sandiego",
        "state": "Alabama",
        "country": "United states"

    }
    ]

    matches = []
    querynorm = query.strip()

    queryfinal = querynorm.lower()

    for merchant in merchantlist:
        if (queryfinal in merchant["name"].lower()
            or queryfinal in merchant["category"].lower()
            or queryfinal in merchant["city"].lower()
            or queryfinal in merchant["state"] or
                queryfinal in merchant["country"].lower()):

            matches.append(merchant)

            print(merchant)

    return matches
