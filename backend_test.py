#!/usr/bin/env python3

import requests
import json
import sys
from datetime import datetime
import time

class ContactFormAPITester:
    def __init__(self):
        # Use the public endpoint from frontend .env
        self.base_url = "https://quest-builder-110.preview.emergentagent.com"
        self.tests_run = 0
        self.tests_passed = 0

    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        if headers is None:
            headers = {'Content-Type': 'application/json'}

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=10)
            else:
                print(f"❌ Unsupported method: {method}")
                return False, {}

            success = response.status_code == expected_status
            
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    response_json = response.json()
                    print(f"   Response: {json.dumps(response_json, indent=2)}")
                    return True, response_json
                except:
                    print(f"   Response (non-JSON): {response.text}")
                    return True, {}
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text}")
                return False, {}

        except requests.exceptions.Timeout:
            print(f"❌ Failed - Request timeout")
            return False, {}
        except requests.exceptions.ConnectionError as e:
            print(f"❌ Failed - Connection error: {str(e)}")
            return False, {}
        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_api_root(self):
        """Test API root endpoint"""
        return self.run_test(
            "API Root",
            "GET",
            "api/",
            200
        )

    def test_contact_form_submission(self):
        """Test contact form submission"""
        test_data = {
            "name": "Test User",
            "email": "test@example.com",
            "message": "This is a test message from the automated testing suite."
        }
        
        return self.run_test(
            "Contact Form Submission",
            "POST",
            "api/contact",
            200,
            data=test_data
        )

    def test_contact_form_validation_missing_name(self):
        """Test contact form validation - missing name"""
        test_data = {
            "email": "test@example.com",
            "message": "This is a test message."
        }
        
        return self.run_test(
            "Contact Form Validation (Missing Name)",
            "POST",
            "api/contact",
            422,  # FastAPI validation error
            data=test_data
        )

    def test_contact_form_validation_invalid_email(self):
        """Test contact form validation - invalid email"""
        test_data = {
            "name": "Test User",
            "email": "invalid-email",
            "message": "This is a test message."
        }
        
        return self.run_test(
            "Contact Form Validation (Invalid Email)",
            "POST",
            "api/contact",
            422,  # FastAPI validation error
            data=test_data
        )

    def test_contact_form_validation_missing_message(self):
        """Test contact form validation - missing message"""
        test_data = {
            "name": "Test User",
            "email": "test@example.com"
        }
        
        return self.run_test(
            "Contact Form Validation (Missing Message)",
            "POST",
            "api/contact",
            422,  # FastAPI validation error
            data=test_data
        )

    def test_get_contact_submissions(self):
        """Test getting contact submissions (admin endpoint)"""
        return self.run_test(
            "Get Contact Submissions",
            "GET",
            "api/contact/submissions",
            200
        )

    def test_multiple_submissions(self):
        """Test multiple contact form submissions"""
        submissions = []
        for i in range(3):
            test_data = {
                "name": f"Test User {i+1}",
                "email": f"test{i+1}@example.com",
                "message": f"This is test message number {i+1}."
            }
            
            success, response = self.run_test(
                f"Contact Form Submission {i+1}",
                "POST",
                "api/contact",
                200,
                data=test_data
            )
            
            if success and 'id' in response:
                submissions.append(response['id'])
            
            # Small delay between submissions
            time.sleep(0.5)
        
        return len(submissions) == 3, submissions

def main():
    print("=" * 60)
    print("XI VENTURES CONTACT FORM API TESTING")
    print("=" * 60)
    
    tester = ContactFormAPITester()
    
    # Test API availability
    print("\n📡 Testing API Connectivity...")
    api_success, _ = tester.test_api_root()
    
    if not api_success:
        print("\n❌ API is not accessible. Cannot proceed with testing.")
        print(f"\n📊 Final Results: {tester.tests_passed}/{tester.tests_run} tests passed")
        return 1
    
    print("\n📝 Testing Contact Form Functionality...")
    
    # Test successful submission
    contact_success, contact_response = tester.test_contact_form_submission()
    submission_id = contact_response.get('id') if contact_success else None
    
    # Test form validation
    print("\n🔍 Testing Form Validation...")
    tester.test_contact_form_validation_missing_name()
    tester.test_contact_form_validation_invalid_email()
    tester.test_contact_form_validation_missing_message()
    
    # Test multiple submissions
    print("\n📋 Testing Multiple Submissions...")
    multiple_success, submission_ids = tester.test_multiple_submissions()
    
    # Test getting submissions
    print("\n📖 Testing Admin Endpoint...")
    submissions_success, submissions_response = tester.test_get_contact_submissions()
    
    # Verify data persistence
    print("\n💾 Verifying Data Persistence...")
    if submissions_success and isinstance(submissions_response, list):
        stored_count = len(submissions_response)
        print(f"   📊 Found {stored_count} stored submissions in database")
        
        if submission_id:
            found = any(sub.get('id') == submission_id for sub in submissions_response)
            if found:
                print(f"   ✅ Original test submission found in database")
                tester.tests_passed += 1
            else:
                print(f"   ❌ Original test submission NOT found in database")
            tester.tests_run += 1
    
    # Print final results
    print("\n" + "=" * 60)
    print("TESTING SUMMARY")
    print("=" * 60)
    print(f"📊 Tests passed: {tester.tests_passed}/{tester.tests_run}")
    
    success_rate = (tester.tests_passed / tester.tests_run * 100) if tester.tests_run > 0 else 0
    print(f"📈 Success rate: {success_rate:.1f}%")
    
    if tester.tests_passed == tester.tests_run:
        print("🎉 All tests passed!")
    elif tester.tests_passed >= tester.tests_run * 0.8:
        print("✅ Most tests passed - minor issues detected")
    else:
        print("⚠️  Significant issues detected - review required")
    
    print("\n📝 NOTES:")
    print("• SendGrid email functionality will fail without API key (expected)")
    print("• Contact form submissions should be stored in MongoDB")
    print("• All validation errors should return HTTP 422")
    
    return 0 if tester.tests_passed >= tester.tests_run * 0.8 else 1

if __name__ == "__main__":
    sys.exit(main())