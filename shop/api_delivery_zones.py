"""
Admin REST API for DeliveryZone CRUD.
All endpoints require IsAdminUser permission.
"""
from rest_framework import generics, permissions, serializers

from shop.models import DeliveryZone


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            'id',
            'name',
            'cities',
            'free_delivery_threshold',
            'delivery_fee',
            'cod_surcharge',
            'estimated_delivery_label',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DeliveryZoneListCreateAPIView(generics.ListCreateAPIView):
    """
    GET  /api/shop/admin/delivery-zones/  -> List all delivery zones
    POST /api/shop/admin/delivery-zones/  -> Create a new delivery zone

    Admin only. No pagination (zone count is always small).
    """

    permission_classes = [permissions.IsAdminUser]
    serializer_class = DeliveryZoneSerializer
    queryset = DeliveryZone.objects.all()


class DeliveryZoneDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/shop/admin/delivery-zones/<pk>/  -> Retrieve a zone
    PATCH  /api/shop/admin/delivery-zones/<pk>/  -> Update a zone (partial)
    PUT    /api/shop/admin/delivery-zones/<pk>/  -> Update a zone (full)
    DELETE /api/shop/admin/delivery-zones/<pk>/  -> Delete a zone

    Admin only.
    """

    permission_classes = [permissions.IsAdminUser]
    serializer_class = DeliveryZoneSerializer
    queryset = DeliveryZone.objects.all()
