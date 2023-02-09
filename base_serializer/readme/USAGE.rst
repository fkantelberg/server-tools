Under  *Settings > Technical > Database Structure > Serializer* you can configure
the serializers used by your implementation.

To use the framework the following methods of the model `ir.serializer` can be used:

* `serialize`: Serializes records with the defined mapping and code snippets
* `deserialize`: Deserializes JSON or a list of dictionaties with the defined mapping and code snippets
* `import_deserialized`: Imports the previously deserialized into the matching records. The given domain is used to find a matching record
