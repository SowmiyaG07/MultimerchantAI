

from app.database.database import get_connection
from sqlalchemy import text


def getcustomerid_email(email: str):
    connection = get_connection()

    db = next(connection)

    try:
        result = db.execute(
            text
            ("""
        
            SELECT 
                CustomerID,
                FirstName,
                LastName,
                Email,
                Password,
                WhiteLabelID,
                Active,
                ExternalID
            FROM dbo.ALL_Get1CardCustomer
            WHERE Email = :email
        """),
            {"email": email},
        )

        row = result.mappings().first()
        if not row:
            return None

        return {
            "customer_id": row["CustomerID"],
            "first_name": row["FirstName"],
            "last_name": row["LastName"],
            "email": row["Email"],
            "password": row["Password"],
             "active": row["Active"],
            "whitelabel_id": row["WhiteLabelID"],
            "external_id": row["ExternalID"]
        }

    finally:
        connection.close()


def getcustomerid(customerid: int):
    # Runs on every chat request: no Password here, it is only needed
    # (and only selected) by the login lookup, getcustomerid_email().
    connection = get_connection()

    db = next(connection)

    try:
        result = db.execute(
            text
            ("""
                  SELECT 
                  CustomerID,
                  FirstName,
                  LastName,
                  WhiteLabelID,
                  Active,
                  ExternalID
                FROM dbo.ALL_Get1CardCustomer
                WHERE CustomerID = :customerid
                         """),
            {"customerid": customerid},
        )
        row = result.mappings().first()
        if not row:
            return None

        return {
            "customer_id": row["CustomerID"],
            "first_name": row["FirstName"],
            "last_name": row["LastName"],
            "active": row["Active"],
            "external_id": row["ExternalID"]
        }

    finally:
        connection.close()
