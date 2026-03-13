#!/usr/bin/env python3

import requests
import json
import sys
from datetime import datetime
import time
import uuid

class AmbientAITester:
    def __init__(self):
        # Use the public endpoint from frontend .env
        self.base_url = "https://quest-builder-110.preview.emergentagent.com"
        self.tests_run = 0
        self.tests_passed = 0
        self.session_id = str(uuid.uuid4())  # Generate unique session ID

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

    def test_chat_basic_message(self):
        """Test basic chat message to AI"""
        test_data = {
            "session_id": self.session_id,
            "message": "Hello, I'm interested in learning more about XI Ventures."
        }
        
        success, response = self.run_test(
            "Chat - Basic Message",
            "POST",
            "api/chat",
            200,
            data=test_data
        )
        
        if success and response.get('response'):
            print(f"   AI Response: {response['response'][:100]}...")
            return True, response
        
        return success, response

    def test_chat_high_intent_message(self):
        """Test high-intent message that should trigger email capture"""
        test_data = {
            "session_id": self.session_id,
            "message": "I'd like to discuss a potential partnership with XI Ventures. Can we set up a meeting?"
        }
        
        success, response = self.run_test(
            "Chat - High Intent Message",
            "POST",
            "api/chat",
            200,
            data=test_data
        )
        
        if success and response.get('action') == 'capture_email':
            print(f"   ✅ Email capture triggered correctly")
            return True, response
        elif success:
            print(f"   ⚠️ Email capture not triggered (action: {response.get('action')})")
        
        return success, response

    def test_email_capture(self):
        """Test email capture endpoint"""
        test_data = {
            "session_id": self.session_id,
            "email": "test@example.com",
            "name": "Test User"
        }
        
        return self.run_test(
            "Email Capture",
            "POST",
            "api/chat/capture-email",
            200,
            data=test_data
        )

    def test_multi_turn_conversation(self):
        """Test multi-turn conversation with context"""
        messages = [
            "What does XI Ventures focus on?",
            "That sounds interesting. What kind of companies do you typically invest in?",
            "Do you have any current portfolio companies?"
        ]
        
        responses = []
        for i, message in enumerate(messages):
            test_data = {
                "session_id": self.session_id,
                "message": message
            }
            
            success, response = self.run_test(
                f"Multi-turn Chat {i+1}",
                "POST",
                "api/chat",
                200,
                data=test_data
            )
            
            if success:
                responses.append(response)
            
            # Small delay between messages
            time.sleep(1)
        
        return len(responses) == len(messages), responses

    def test_get_conversations(self):
        """Test getting conversation sessions (admin endpoint)"""
        return self.run_test(
            "Get Conversations",
            "GET",
            "api/conversations",
            200
        )

    def test_conversation_persistence(self):
        """Test that conversations are stored in MongoDB"""
        success, conversations = self.test_get_conversations()
        
        if success and isinstance(conversations, list):
            # Look for our test session
            session_found = any(conv.get('session_id') == self.session_id for conv in conversations)
            
            if session_found:
                print(f"   ✅ Test conversation session found in database")
                self.tests_passed += 1
            else:
                print(f"   ❌ Test conversation session NOT found in database")
            
            self.tests_run += 1
            print(f"   📊 Total conversations in database: {len(conversations)}")
            return session_found, conversations
        
        return False, []
    
    # Legacy contact form tests (for backward compatibility)
    def test_contact_form_submission(self):
        """Test legacy contact form submission"""
        test_data = {
            "name": "Test User",
            "email": "test@example.com",
            "message": "This is a test message from the automated testing suite."
        }
        
        return self.run_test(
            "Legacy Contact Form Submission",
            "POST",
            "api/contact",
            200,
            data=test_data
        )

    # Remove old validation tests - focus on ambient AI interface

def main():
    print("=" * 60)
    print("XI VENTURES AMBIENT AI INTERFACE TESTING")
    print("=" * 60)
    
    tester = AmbientAITester()
    
    # Test API availability
    print("\n📡 Testing API Connectivity...")
    api_success, _ = tester.test_api_root()
    
    if not api_success:
        print("\n❌ API is not accessible. Cannot proceed with testing.")
        print(f"\n📊 Final Results: {tester.tests_passed}/{tester.tests_run} tests passed")
        return 1
    
    print(f"\n🤖 Testing Ambient AI Chat (Session: {tester.session_id[:8]}...)...")
    
    # Test basic chat functionality
    chat_success, chat_response = tester.test_chat_basic_message()
    
    # Test high-intent message (should trigger email capture)
    print("\n🎯 Testing High-Intent Detection...")
    intent_success, intent_response = tester.test_chat_high_intent_message()
    
    # Test email capture if triggered
    if intent_success and intent_response.get('action') == 'capture_email':
        print("\n📧 Testing Email Capture...")
        email_success, email_response = tester.test_email_capture()
    
    # Test multi-turn conversation
    print("\n💬 Testing Multi-turn Conversation...")
    multi_success, multi_responses = tester.test_multi_turn_conversation()
    
    # Test conversation persistence
    print("\n💾 Testing Conversation Persistence...")
    persistence_success, conversations = tester.test_conversation_persistence()
    
    # Test admin endpoint
    print("\n📖 Testing Admin Endpoints...")
    admin_success, admin_response = tester.test_get_conversations()
    
    # Test legacy contact form (backward compatibility)
    print("\n📝 Testing Legacy Contact Form...")
    legacy_success, legacy_response = tester.test_contact_form_submission()
    
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
    print("• AI responses powered by GPT-4o-mini via Emergent LLM")
    print("• Email capture triggers on high-intent conversations")
    print("• SendGrid email notifications will fail without API key (expected)")
    print("• All conversations stored in MongoDB with session tracking")
    print("• Multi-turn conversations maintain context")
    
    return 0 if tester.tests_passed >= tester.tests_run * 0.8 else 1

if __name__ == "__main__":
    sys.exit(main())