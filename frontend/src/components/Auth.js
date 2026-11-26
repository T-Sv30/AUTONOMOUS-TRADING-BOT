import React, { useState } from 'react';
import axios from 'axios';
import { API } from '../App';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { toast } from 'sonner';
import { TrendingUp, Shield, Zap, Brain } from 'lucide-react';

const Auth = ({ onLogin }) => {
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      const endpoint = isLogin ? `${API}/auth/login` : `${API}/auth/signup`;
      const response = await axios.post(endpoint, { email, password });
      
      const { access_token, user } = response.data;
      onLogin(access_token, user);
      toast.success(`Welcome ${isLogin ? 'back' : 'aboard'}!`);
    } catch (error) {
      const message = error.response?.data?.detail || 'Authentication failed';
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative">
      <div className="w-full max-w-6xl grid md:grid-cols-2 gap-8 items-center">
        {/* Left Side - Hero */}
        <div className="space-y-8 text-center md:text-left">
          <div>
            <h1 className="text-5xl md:text-6xl font-bold mb-4 bg-gradient-to-r from-primary to-blue-400 bg-clip-text text-transparent" data-testid="hero-title">
              Autonomous Trading Bot
            </h1>
            <p className="text-lg md:text-xl text-muted-foreground" data-testid="hero-subtitle">
              AI-powered crypto trading with GPT-5 analysis and intelligent risk management
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="glass-card p-4 rounded-lg">
              <Brain className="w-8 h-8 text-primary mb-2" />
              <h3 className="font-semibold mb-1">GPT-5 Analysis</h3>
              <p className="text-sm text-muted-foreground">Real-time market intelligence</p>
            </div>
            <div className="glass-card p-4 rounded-lg">
              <Shield className="w-8 h-8 text-primary mb-2" />
              <h3 className="font-semibold mb-1">Risk Protection</h3>
              <p className="text-sm text-muted-foreground">Drawdown and daily-loss safeguards</p>
            </div>
            <div className="glass-card p-4 rounded-lg">
              <TrendingUp className="w-8 h-8 text-primary mb-2" />
              <h3 className="font-semibold mb-1">Smart Execution</h3>
              <p className="text-sm text-muted-foreground">Optimized trade sizing</p>
            </div>
            <div className="glass-card p-4 rounded-lg">
              <Zap className="w-8 h-8 text-primary mb-2" />
              <h3 className="font-semibold mb-1">Automated</h3>
              <p className="text-sm text-muted-foreground">24/7 market monitoring</p>
            </div>
          </div>
        </div>

        {/* Right Side - Auth Form */}
        <Card className="glass-card p-8 border-2" data-testid="auth-card">
          <div className="mb-6">
            <h2 className="text-3xl font-bold mb-2" data-testid="auth-form-title">
              {isLogin ? 'Welcome Back' : 'Get Started'}
            </h2>
            <p className="text-muted-foreground">
              {isLogin ? 'Sign in to your account' : 'Create your trading account'}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-2 block">Email</label>
              <Input
                type="email"
                data-testid="email-input"
                placeholder="your@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full"
              />
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">Password</label>
              <Input
                type="password"
                data-testid="password-input"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full"
              />
            </div>

            <Button
              type="submit"
              data-testid="auth-submit-button"
              className="w-full btn-primary"
              disabled={loading}
            >
              {loading ? 'Processing...' : isLogin ? 'Sign In' : 'Create Account'}
            </Button>
          </form>

          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => setIsLogin(!isLogin)}
              data-testid="toggle-auth-mode"
              className="text-sm text-primary hover:underline"
            >
              {isLogin ? "Don't have an account? Sign up" : 'Already have an account? Sign in'}
            </button>
          </div>
        </Card>
      </div>
    </div>
  );
};

export default Auth;
