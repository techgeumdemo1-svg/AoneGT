from decimal import Decimal

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import DeliveryZone

from .orders import _paginate_queryset
from .views import IsStaffUser


def _delivery_zones_queryset():
    return DeliveryZone.objects.all()


class AdminDeliveryZoneListSerializer(serializers.ModelSerializer):
    zone_id = serializers.IntegerField(source="id", read_only=True)
    cities_count = serializers.SerializerMethodField()
    free_delivery_threshold_aed = serializers.SerializerMethodField()
    delivery_fee_aed = serializers.SerializerMethodField()
    cod_surcharge_aed = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryZone
        fields = (
            "zone_id",
            "name",
            "cities",
            "cities_count",
            "free_delivery_threshold_aed",
            "delivery_fee_aed",
            "cod_surcharge_aed",
            "estimated_delivery_label",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_cities_count(self, obj):
        return len(obj.cities or [])

    def get_free_delivery_threshold_aed(self, obj):
        return str(Decimal(str(obj.free_delivery_threshold)).quantize(Decimal("0.01")))

    def get_delivery_fee_aed(self, obj):
        return str(Decimal(str(obj.delivery_fee)).quantize(Decimal("0.01")))

    def get_cod_surcharge_aed(self, obj):
        return str(Decimal(str(obj.cod_surcharge)).quantize(Decimal("0.01")))


class AdminDeliveryZoneWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = (
            "name",
            "cities",
            "free_delivery_threshold",
            "delivery_fee",
            "cod_surcharge",
            "estimated_delivery_label",
            "is_active",
        )

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Name is required.")
        return name

    def validate_cities(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("cities must be a list of strings.")
        cleaned = []
        seen = set()
        for item in value:
            city = str(item).strip()
            if not city:
                continue
            key = city.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(city)
        if not cleaned:
            raise serializers.ValidationError("At least one city or area is required.")
        return cleaned

    def validate_free_delivery_threshold(self, value):
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
        if amount < 0:
            raise serializers.ValidationError("Must be zero or greater.")
        return amount

    def validate_delivery_fee(self, value):
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
        if amount < 0:
            raise serializers.ValidationError("Must be zero or greater.")
        return amount

    def validate_cod_surcharge(self, value):
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
        if amount < 0:
            raise serializers.ValidationError("Must be zero or greater.")
        return amount


class AdminDeliveryZoneToggleSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)


def _apply_delivery_zone_list_filters(queryset, request):
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = Q(name__icontains=search) | Q(estimated_delivery_label__icontains=search)
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset.order_by("name")


def _zone_payload(zone: DeliveryZone) -> dict:
    return AdminDeliveryZoneListSerializer(zone).data


class AdminDeliveryZoneListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_delivery_zone_list_filters(_delivery_zones_queryset(), request)
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminDeliveryZoneListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = AdminDeliveryZoneWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        zone = serializer.save()
        return Response(
            {
                "message": "Delivery zone created.",
                "zone": _zone_payload(zone),
            },
            status=status.HTTP_201_CREATED,
        )


class AdminDeliveryZoneDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        zone = get_object_or_404(_delivery_zones_queryset(), pk=pk)
        serializer = AdminDeliveryZoneWriteSerializer(zone, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        zone = serializer.save()
        return Response(
            {
                "message": "Delivery zone updated.",
                "zone": _zone_payload(zone),
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        zone = get_object_or_404(_delivery_zones_queryset(), pk=pk)
        zone_id = zone.pk
        zone.delete()
        return Response(
            {"message": "Delivery zone deleted.", "zone_id": zone_id},
            status=status.HTTP_200_OK,
        )


class AdminDeliveryZoneToggleAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        zone = get_object_or_404(_delivery_zones_queryset(), pk=pk)
        serializer = AdminDeliveryZoneToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if "is_active" in serializer.validated_data:
            zone.is_active = serializer.validated_data["is_active"]
        else:
            zone.is_active = not zone.is_active

        zone.save(update_fields=["is_active", "updated_at"])
        return Response(
            {
                "message": "Delivery zone visibility updated.",
                "zone_id": zone.pk,
                "is_active": zone.is_active,
                "zone": _zone_payload(zone),
            },
            status=status.HTTP_200_OK,
        )
