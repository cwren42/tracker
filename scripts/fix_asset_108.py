from app import db, Asset

asset = Asset.query.get(108)
if asset:
    asset.manufacturer = 'Dell'
    asset.model = 'XPS 15 9520'
    asset.serial_number = 'GJBBLR3'
    db.session.commit()
    print('Asset 108 updated successfully')
else:
    print('Asset 108 not found')
