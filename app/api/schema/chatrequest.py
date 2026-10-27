from pydantic import BaseModel, Field,field_validator,model_validator
from datetime import date



class chatrequest(BaseModel):
    message : str = Field(...,min_length=2,max_length=2000)
    session_id : str | None = None
    customer_id : str | None = None
    latitude : float | None = Field (None,ge=-90,le=90)
    longitude : float | None = Field(None,ge=-180 , le = 180)
    
    from_date : date | None = None
    to_date : date | None = None
    
    @field_validator("from_date","to_date",mode="before")
    @classmethod
    def validatedate(cls,value):
        if value is None:
            return None
        if isinstance(value,str):
            if not value.strip():
                return None
        return value
    
    @model_validator(mode="after")
    def _validatedaterange(self):
        if self.from_date and self.to_date and self.from_date > self.to_date :
            raise ValueError("From_date cannot be greater than to date")
        return self
    
         
    
class chatresponse(BaseModel):
    answer : str 
    session_id : str
    trace_id : str