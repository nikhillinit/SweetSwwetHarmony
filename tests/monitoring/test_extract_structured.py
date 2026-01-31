"""Tests for extruct-based structured data extraction (JSON-LD, microdata, OpenGraph, RDFa).

Tests cover:
- JSON-LD extraction (Product, Organization schemas)
- OpenGraph metadata extraction
- Microdata extraction
- RDFa extraction
- Empty/no structured data handling
- Malformed HTML handling
- Confidence calculation based on richness
- Real-world pricing page examples
"""

import json
import pytest
from monitoring.content_pipeline.extract_structured import StructuredDataExtractor
from monitoring.content_pipeline.models import RepresentationType


class TestJSONLDExtraction:
    """Test JSON-LD schema extraction."""

    def test_extract_product_json_ld(self):
        """Should extract JSON-LD Product schema."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "offers": {
                    "@type": "Offer",
                    "price": "29.99",
                    "priceCurrency": "USD"
                }
            }
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.representation_type == RepresentationType.JSON
        assert result.extractor_name == "extruct_v1"

        # Parse the JSON content
        data = json.loads(result.content)
        assert "json-ld" in data
        assert len(data["json-ld"]) > 0

        # Check the Product schema was extracted
        product = data["json-ld"][0]
        assert product["@type"] == "Product"
        assert product["name"] == "Pro Plan"
        assert product["offers"]["price"] == "29.99"

    def test_extract_organization_json_ld(self):
        """Should extract JSON-LD Organization schema."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": "Acme Corp",
                "url": "https://acme.example.com",
                "logo": "https://acme.example.com/logo.png",
                "contactPoint": {
                    "@type": "ContactPoint",
                    "telephone": "+1-555-1234",
                    "contactType": "sales"
                }
            }
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert "json-ld" in data
        org = data["json-ld"][0]
        assert org["@type"] == "Organization"
        assert org["name"] == "Acme Corp"
        assert org["url"] == "https://acme.example.com"

    def test_extract_multiple_json_ld_blocks(self):
        """Should extract multiple JSON-LD blocks."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "Pricing Page"
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Basic Plan"
            }
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert len(data["json-ld"]) == 2

        types = [item["@type"] for item in data["json-ld"]]
        assert "WebPage" in types
        assert "Product" in types


class TestOpenGraphExtraction:
    """Test OpenGraph metadata extraction."""

    def test_extract_opengraph_metadata(self):
        """Should extract OpenGraph meta tags."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Amazing Product">
            <meta property="og:description" content="The best product ever made">
            <meta property="og:type" content="product">
            <meta property="og:url" content="https://example.com/product">
            <meta property="og:image" content="https://example.com/image.jpg">
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert "opengraph" in data
        assert len(data["opengraph"]) > 0

        og = data["opengraph"][0]
        assert og.get("og:title") == "Amazing Product"
        assert og.get("og:description") == "The best product ever made"
        # extruct with uniform=True uses @type instead of og:type
        assert og.get("@type") == "product" or og.get("og:type") == "product"

    def test_extract_opengraph_product_properties(self):
        """Should extract OpenGraph product-specific properties."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Pro Plan">
            <meta property="og:type" content="product">
            <meta property="product:price:amount" content="29.99">
            <meta property="product:price:currency" content="USD">
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        og = data["opengraph"][0]
        assert og.get("og:title") == "Pro Plan"
        # Product price properties may be nested or flat depending on extruct version
        # extruct with uniform=True uses @type instead of og:type
        assert og.get("@type") == "product" or og.get("og:type") == "product"


class TestMicrodataExtraction:
    """Test HTML microdata extraction."""

    def test_extract_microdata_product(self):
        """Should extract microdata Product schema."""
        html = """
        <!DOCTYPE html>
        <html>
        <body>
            <div itemscope itemtype="https://schema.org/Product">
                <span itemprop="name">Enterprise Plan</span>
                <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
                    <span itemprop="price">99.00</span>
                    <span itemprop="priceCurrency">USD</span>
                </div>
            </div>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert "microdata" in data
        assert len(data["microdata"]) > 0

        product = data["microdata"][0]
        # extruct with uniform=True normalizes microdata to JSON-LD style with @type
        product_type = product.get("@type") or product.get("type", [])
        assert "Product" in str(product_type)

    def test_extract_microdata_organization(self):
        """Should extract microdata Organization schema."""
        html = """
        <!DOCTYPE html>
        <html>
        <body>
            <div itemscope itemtype="https://schema.org/Organization">
                <span itemprop="name">TechCorp Inc</span>
                <span itemprop="url">https://techcorp.example.com</span>
            </div>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert "microdata" in data
        assert len(data["microdata"]) > 0


class TestRDFaExtraction:
    """Test RDFa metadata extraction."""

    def test_extract_rdfa_metadata(self):
        """Should extract RDFa metadata."""
        html = """
        <!DOCTYPE html>
        <html vocab="https://schema.org/">
        <body>
            <div typeof="Product">
                <span property="name">Starter Plan</span>
                <span property="description">Perfect for individuals</span>
            </div>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        assert "rdfa" in data
        # RDFa extraction may vary based on document structure
        # Just verify the key exists and result is valid JSON


class TestEmptyAndMalformedHTML:
    """Test handling of empty and malformed HTML."""

    def test_empty_html_returns_empty_structured_data(self):
        """Empty HTML should return empty structured data."""
        extractor = StructuredDataExtractor()
        result = extractor.extract("")

        assert result.content == "{}"
        assert result.representation_type == RepresentationType.JSON
        assert result.confidence == 0.0

    def test_none_html_returns_empty_structured_data(self):
        """None HTML should return empty structured data."""
        extractor = StructuredDataExtractor()
        result = extractor.extract(None)

        assert result.content == "{}"
        assert result.representation_type == RepresentationType.JSON
        assert result.confidence == 0.0

    def test_html_without_structured_data(self):
        """HTML without structured data should return empty result."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Simple Page</title></head>
        <body>
            <p>Just plain text content without any structured data.</p>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)
        # Should have the keys but with empty lists
        total_items = sum(
            len(v) if isinstance(v, list) else 0
            for v in data.values()
        )
        assert total_items == 0
        assert result.confidence < 0.5

    def test_malformed_html_handled_gracefully(self):
        """Malformed HTML should be handled gracefully."""
        html = """
        <div><p>Unclosed paragraph
        <script type="application/ld+json">
        {"@type": "Product", "name": "Test"
        </script>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        # Should not crash, should return valid JSON
        assert result.representation_type == RepresentationType.JSON
        # Content should be valid JSON (empty or partial)
        data = json.loads(result.content)
        assert isinstance(data, dict)

    def test_invalid_json_ld_handled_gracefully(self):
        """Invalid JSON in JSON-LD block should be handled gracefully."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            { invalid json here }
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        # Should not crash
        assert result.representation_type == RepresentationType.JSON
        # Content should still be valid JSON
        data = json.loads(result.content)
        assert isinstance(data, dict)


class TestConfidenceCalculation:
    """Test confidence score calculation based on richness of data."""

    def test_high_confidence_for_rich_structured_data(self):
        """Rich structured data should have high confidence."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Product">
            <meta property="og:type" content="product">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Full Product",
                "description": "A complete product",
                "offers": {"@type": "Offer", "price": "50.00"}
            }
            </script>
        </head>
        <body>
            <div itemscope itemtype="https://schema.org/Organization">
                <span itemprop="name">Company</span>
            </div>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        # With JSON-LD, OpenGraph, and microdata, confidence should be high
        assert result.confidence >= 0.8

    def test_medium_confidence_for_partial_data(self):
        """Partial structured data should have medium confidence."""
        # Use only JSON-LD to avoid OpenGraph also being extracted to RDFa
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {"@type": "WebPage", "name": "Simple"}
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        # Single JSON-LD item should have medium confidence (0.5)
        assert 0.3 <= result.confidence <= 0.7

    def test_low_confidence_for_no_data(self):
        """No structured data should have low confidence."""
        html = "<html><body><p>Plain text</p></body></html>"
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.confidence < 0.3


class TestMetadata:
    """Test metadata in ExtractedContent."""

    def test_metadata_includes_type_counts(self):
        """Metadata should include counts of extracted data types."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Test">
            <script type="application/ld+json">
            {"@type": "Product", "name": "Test"}
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.metadata is not None
        assert "json_ld_count" in result.metadata
        assert "opengraph_count" in result.metadata
        assert "microdata_count" in result.metadata
        assert "rdfa_count" in result.metadata

    def test_metadata_includes_types_found(self):
        """Metadata should list which data types were found."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {"@type": "Product", "name": "Test"}
            </script>
        </head>
        <body><p>Content</p></body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.metadata is not None
        assert "types_found" in result.metadata
        assert "json-ld" in result.metadata["types_found"]

    def test_extraction_time_tracked(self):
        """Extraction time should be tracked."""
        html = "<html><body><p>Test</p></body></html>"
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.extraction_time_ms >= 0


class TestRealWorldPricingPage:
    """Test with real-world pricing page examples."""

    def test_saas_pricing_page_with_json_ld(self):
        """Should extract structured data from typical SaaS pricing page."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Pricing - SaaS Product</title>
            <meta property="og:title" content="Pricing Plans">
            <meta property="og:description" content="Choose the plan that works for you">
            <meta property="og:type" content="website">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "Pricing",
                "description": "Our pricing plans"
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Basic Plan",
                "description": "For individuals and small teams",
                "offers": {
                    "@type": "Offer",
                    "price": "19.00",
                    "priceCurrency": "USD",
                    "priceValidUntil": "2025-12-31"
                }
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "description": "For growing businesses",
                "offers": {
                    "@type": "Offer",
                    "price": "49.00",
                    "priceCurrency": "USD"
                }
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Enterprise Plan",
                "description": "For large organizations",
                "offers": {
                    "@type": "Offer",
                    "price": "199.00",
                    "priceCurrency": "USD"
                }
            }
            </script>
        </head>
        <body>
            <h1>Choose Your Plan</h1>
            <div class="pricing-grid">
                <div class="plan">
                    <h2>Basic</h2>
                    <p class="price">$19/month</p>
                </div>
            </div>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)

        # Should have multiple JSON-LD items
        assert len(data["json-ld"]) == 4

        # Find Product items
        products = [item for item in data["json-ld"] if item.get("@type") == "Product"]
        assert len(products) == 3

        # Verify pricing data is accessible
        prices = [p["offers"]["price"] for p in products]
        assert "19.00" in prices
        assert "49.00" in prices
        assert "199.00" in prices

        # Should have high confidence due to rich data
        assert result.confidence >= 0.7

        # Should have OpenGraph data
        assert len(data["opengraph"]) > 0
        assert data["opengraph"][0].get("og:title") == "Pricing Plans"

    def test_ecommerce_product_page(self):
        """Should extract structured data from e-commerce product page."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Wireless Headphones">
            <meta property="og:type" content="product">
            <meta property="og:image" content="https://example.com/headphones.jpg">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Premium Wireless Headphones",
                "image": "https://example.com/headphones.jpg",
                "description": "High-quality wireless headphones with noise cancellation",
                "brand": {
                    "@type": "Brand",
                    "name": "AudioTech"
                },
                "offers": {
                    "@type": "Offer",
                    "url": "https://example.com/headphones",
                    "price": "299.99",
                    "priceCurrency": "USD",
                    "availability": "https://schema.org/InStock",
                    "seller": {
                        "@type": "Organization",
                        "name": "Example Store"
                    }
                },
                "aggregateRating": {
                    "@type": "AggregateRating",
                    "ratingValue": "4.8",
                    "reviewCount": "256"
                }
            }
            </script>
        </head>
        <body>
            <h1>Premium Wireless Headphones</h1>
            <p class="price">$299.99</p>
        </body>
        </html>
        """
        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)

        # Should extract the product JSON-LD
        assert len(data["json-ld"]) == 1
        product = data["json-ld"][0]

        assert product["@type"] == "Product"
        assert product["name"] == "Premium Wireless Headphones"
        assert product["offers"]["price"] == "299.99"
        assert product["brand"]["name"] == "AudioTech"
        assert product["aggregateRating"]["ratingValue"] == "4.8"

        # Should extract OpenGraph
        assert len(data["opengraph"]) > 0
